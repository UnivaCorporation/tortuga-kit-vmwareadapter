#!/usr/bin/python

# Copyright 2008-2018 Univa Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# pylint: disable=no-member,maybe-no-member,protected-access

import os.path
import random
import configparser
import itertools
import time
import socket
import uuid

import pysphere
from pysphere import VITask, VIMor, MORTypes, VIProperty
from pysphere.resources import VimService_services as VI
from pysphere.resources.vi_exception import VIException, VIApiException, \
    FaultTypes
from pysphere.vi_virtual_machine import VIVirtualMachine

from tortuga.resourceAdapter.resourceAdapter import ResourceAdapter
from tortuga.exceptions.tortugaException import TortugaException
from tortuga.exceptions.invalidArgument import InvalidArgument
from tortuga.exceptions.commandFailed import CommandFailed
from tortuga.exceptions.noFreeResourcesAvailable \
    import NoFreeResourcesAvailable
from tortuga.exceptions.missingHypervisorNetwork \
    import MissingHypervisorNetwork
from tortuga.exceptions.unsupportedOperation import UnsupportedOperation
from tortuga.exceptions.virtualMachineNotFound import VirtualMachineNotFound
from tortuga.exceptions.networkNotFound import NetworkNotFound
from tortuga.os_utility import tortugaSubprocess


class Vmware_adapter(ResourceAdapter):
    __adaptername__ = 'vmware'

    HYPERVISOR_RAM = 512
    DATASTORE = 'UCDatastore'

    DEFAULT_RAM_MB = 512
    DEFAULT_N_CPUS = 1
    DEFAULT_DISK_GB = 8

    VLAN_DATA_FILE = 'vmware-vlan-data.conf'

    def __init__(self, addHostSession=None):
        super(Vmware_adapter, self).__init__(addHostSession=addHostSession)

        self.__sanApi = None
        self.__dataStore = Vmware_adapter.DATASTORE
        self.looping = False

        self.__vCenterHostName = None
        self.__vCenterUserName = None
        self.__vCenterPassword = None
        self.__vCenterDataCenter = None
        self.__destinationPath = None
        self.__vCenterPool = None
        self.__vCenterCluster = None

        self._server = None
        self.nConnectionCount = 0

        self.__vCenter_cfgfile = os.path.join(
            self._cm.getKitConfigBase(), 'vmware_vcenter_auth.conf')

        self.__loadConfiguration(self.__vCenter_cfgfile)

        self.fqdn = socket.getfqdn()

        self._domainname = self.fqdn.split('.', 1)[1] \
            if '.' in self.fqdn else None

        self.instance_cache_path = os.path.join(self._cm.getKitConfigBase(),
                                                'vmware-instance.conf')

    def isLiveMigrateable(self):    # pylint: disable=no-self-use
        return True

    def suspendActiveNode(self, dbNode):
        '''Suspend node'''
        self.__suspendVm(self.__getVmName(dbNode))

    def idleActiveNode(self, dbNodes):
        '''Change the given active node to an idle node'''

        self.getLogger().debug(
            '[vmware] idleActiveNode(dbNodes=[%s])' % (
                ', '.join([dbNode.name for dbNode in dbNodes])))

        # FYI... Because this resource adapter supports VM suspend, there
        # is no VM state transition required here. Suspend is called prior
        # to calling idleActiveNode(). The suspended node remains in
        # Installed state.

        return 'Installed'

    def __connectDisks(self, node, typeList, hypervisorName):
        self.getLogger().debug(
            '[vmware] __connectDisks(node=[%s], typeList=[%s],'
            ' hypervisorName=[%s])' % (node.name, typeList, hypervisorName))

        # Get disks
        disks = self.sanApi.discoverStorageChanges(node)

        vmname = self.__getVmName(node.name)

        # Connect disks from hypervisor if needed
        for type_ in typeList:
            #Need to make sure we go in order adding the disks
            diskNumbers = sorted(disks[type_].keys())
            for diskNumber in diskNumbers:
                storageAdapter = disks[type_][diskNumber]['adapter']
                if storageAdapter != 'default':
                    # Get the device path
                    device = self.sanApi.connectStorage(
                        node, diskNumber, hypervisorName)

                    diskPath = 'phy:%s' % device
                else:
                    vmPath = self.__getVmPath(vmname)

                    diskPath = '%s/%s-System.img' % (vmPath, diskNumber)

                self.__addDiskToVm(node, diskPath, (int(diskNumber) - 1))

    def activateIdleNode(self, dbNode, softwareProfileName,
                         softwareProfileChanged): \
            # pylint: disable=unused-argument
        '''Change the given idle node to an active node'''

        # Get the node
        bBootLocal = False if dbNode.state == 'Discovered' else \
            not softwareProfileChanged

        self.getLogger().debug(
            '[vmware] activateIdleNode(): node=[%s],'
            ' reprovision=[%s]' % (dbNode.name, not bBootLocal))

        # Create PXE files
        self.osObject.getOsBootHostManager().writePXEFile(
            dbNode, localboot=bBootLocal)

    def deleteNode(self, dbNodes):
        '''Remove the given active node from the system'''

        for node in dbNodes:
            try:
                # Make sure the vm is stopped
                self.__stopVm(node, True)
            except VirtualMachineNotFound:
                # Our work is already done!
                continue
            except CommandFailed:
                self.getLogger().warning(
                    '[vmware] Unable to stop VM [%s], attempting to'
                    ' destroy' % (node.name))

            self.__destroyVm(node)

            # Remove the recently deleted node from the instance cache
            self.__instance_cache_set(node.name)

    def transferNode(self, nodeSoftwareProfileTuplesId,
                     newSoftwareProfileName): \
            # pylint: disable=unused-argument
        """
        Raises:
            CommandFailed
            NodeNotFound
        """

        server = self.__connect_vsphere()

        for dbNode, _ in nodeSoftwareProfileTuplesId: \
                # pylint: disable=unused-variable
            vmname = self.__getVmName(dbNode)

            # Set the PXE config file to reinstall the node
            self.osObject.getOsBootHostManager().\
                setNodeForNetworkBoot(dbNode)

            try:
                vm = self.__get_vm_by_name(server, vmname)

                self.__resetVmCommand(vm)
            except VirtualMachineNotFound:
                # This would indicate that someone pulled the rug out
                # from under us (ie. manually removed a VM from the
                # hypervisor).

                self.getLogger().warning(
                    '[vmware] VM [%s] not found; ignoring' % (vmname))

                continue
            except CommandFailed as ex:
                self.getLogger().warning(
                    '[vmware] Error resetting node [%s]: %s' % (
                        vmname, ex))

        self.__disconnect_vsphere()

    def migrateNode(self, dbNode, remainingNodeList, liveMigrate=True):
        if liveMigrate:
            if not self.isLiveMigrateable():
                raise UnsupportedOperation(
                    'Node [%s] does not support live migration' % (
                        dbNode.name))

        # Migrate the VM
        self.__migrateVm(dbNode, remainingNodeList, liveMigrate)

    def shutdownNode(self, dbNodes, bSoftShutdown=False):
        # Iterate over list of node IDs and shutdown/stop each
        # associated VM
        for node in dbNodes:
            self.__stopVm(node, bHard=not bSoftShutdown)

    def startupNode(self, dbNodes, nodeList=None, tempBootMethod='n'): \
            # pylint: disable=unused-argument
        for node in dbNodes:
            self.__startVm(node, nodeList or [])

    def rebootNode(self, dbNodes, bSoftReset=False): \
            # pylint: disable=unused-argument
        """
        TODO: create list of all VMs to ensure they all exist prior to
        attempting to reset them. This will make the operation atomic.
        """

        server = self.__connect_vsphere()

        try:
            for vmname in [self.__getVmName(dbNode) for dbNode in dbNodes]:
                try:
                    vm = self.__get_vm_by_name(server, vmname)
                except VirtualMachineNotFound:
                    self.getLogger().error(
                        '[vmware] VM [%s] not found; ignoring' % (vmname))

                    continue

                # Reboot the VM
                self.__resetVmCommand(vm)
        finally:
            self.__disconnect_vsphere()

    def __get_vm_by_name(self, server, vmname):
        """
        Raises:
            CommandFailed
            VirtualMachineNotFound
        """

        try:
            vm = server.get_vm_by_name(vmname)
        except pysphere.VIException as ex:
            if ex.fault != pysphere.FaultTypes.OBJECT_NOT_FOUND:
                errmsg = 'Unable to get VM [%s] (err=%s)' % (
                    vmname, ex.message)

                self.getLogger().error('[vmware] %s' % (errmsg))

                raise CommandFailed(errmsg)

            raise VirtualMachineNotFound(
                'VM [%s] not found (err=%s)' % (vmname, ex.message))

        return vm

    def checkpointNode(self, dbNode):
        '''
        Checkpoint the given node

        Raises:
            CommandFailed
        '''

        vmname = self.__getVmName(dbNode)

        self.getLogger().debug(
            '[vmware] Checkpointing VM [%s]' % (vmname))

        server = self.__connect_vsphere()

        try:
            vm = self.__get_vm_by_name(server, vmname)

            # Delete old snapshot (if any), then create new one
            self.__deleteSnapshotCommand(vm)
            self.__createSnapshotCommand(vm)
        finally:
            self.__disconnect_vsphere()

        self.getLogger().debug(
            "[vmware] Checkpointing of VM [%s] complete" % (vmname))

    def revertNodeToCheckpoint(self, dbNode):
        '''
        Revert the given node to its checkpoint
        '''

        vmname = self.__getVmName(dbNode)

        self.getLogger().debug(
            "[vmware] Revert VM [%s] to checkpoint" % (vmname))

        server = self.__connect_vsphere()

        try:
            vm = self.__get_vm_by_name(server, vmname)

            # Revert to snapshot
            self.__revertSnapshotCommand(vm)
        finally:
            self.__disconnect_vsphere()

        self.getLogger().debug(
            "[vmware] Revert VM [%s] to checkpoint complete" % (vmname))

    def __getHypervisor(self, hwProfile):
        """
        Return hypervisor for hardware profile, if defined, otherwise
        return None.

        Raises:
            UnsupportedOperation
        """

        # Search for at least one hypervisor
        self.__validateHypervisor(hwProfile)

        # if not dbHardwareProfile.nics:
        #     # TODO: fix this error message
        #     raise NicNotFound(
        #         'No provisioning NIC found for nodes hypervisor')

        if not hwProfile.hypervisor:
            return None

        if not hwProfile.hypervisor.nodes:
            raise UnsupportedOperation(
                'No hypervisors available for hardware profile [%s]' % (
                    hwProfile.name))

        # Select one, doesn't matter which one as its only used to find
        # the list of all available
        return hwProfile.hypervisor.nodes[0]

    def __getVmPath(self, vmname):
        return '[%(datastore)s] %(vmname)s/%(vmname)s.vmx' % ({
            'datastore': self.__dataStore,
            'vmname': vmname,
        })

    def __startVm(self, node, remainingNodeList=None,
                  bThinProvision=False, hardwareprofile=None,
                  softwareprofile=None):
        """ Start a vm """

        self.getLogger().debug('[vmware] Starting node [%s]' % (node.name))

        # When we don't have a target host we need to be a little
        # fancier...
        tgt_host = None

        if remainingNodeList:
            tgt_host = remainingNodeList[0]
        elif node.parentnode is not None:
            tgt_host = node.parentnode

        # if tgt_host is None:
        #     hypervisorSoftwareProfileName = \
        #         self.__getHypervisorSoftwareProfileName(node)

        #     tgt_host = self.nodeApi.getNode(
        #         self.__getDedicatedServer(
        #             hypervisorSoftwareProfileName, node,
        #             remainingNodeList=remainingNodeList))

        #     self.getLogger().debug(
        #         '[vmware] Starting node [%s] on hypervisor [%s]' % (
        #             node, tgt_host))

        # Need migrate checks
        # bNeedMigrate = True if parentNodeId is None \
        #     else tgt_host.getId() != parentNodeId

        # self.getLogger().debug(
        #     '[vmware] __startVm(): bNeedMigrate=[%s]' % (bNeedMigrate))

        # if bNeedMigrate:
        #     # got to migrate first... this will also take care of the vlan
        #     # setup...
        #     self.__migrateVm(node, [tgt_host.getName()])
        # else:
        #     # Set up VLAN(s)
        #     self.__connectVlan(node, tgt_host)

        vmname = self.__getVmName(node)

        # --- Start Disk Stuff ---

        # Get disk changes
        # At this time, only non-SAN disks are supported
        diskChanges = self.sanApi.discoverStorageChanges(
            node,
            hardwareprofile=hardwareprofile
            if hardwareprofile else node.hardwareprofile,
            softwareprofile=softwareprofile
            if softwareprofile else node.softwareprofile)

        # Process deletes
        #self.__disconnectDisks(node, ['removed'], serverProxy=None)
        #self.__processDeletedDiskChanges(node, diskChanges=diskChanges)

        # Process adds
        self.__processAddedDiskChanges(node, diskChanges,
                                       bThinProvision=bThinProvision)

        #self.__connectDisks(node, ['unchanged', 'added'], tgt_host)

        # --- End Disk Stuff ---

        server = self.__connect_vsphere()

        try:
            vm = self.__get_vm_by_name(server, vmname)

            if vm.is_powered_on():
                self.getLogger().info(
                    '[vmware] VM [%s] already powered on; ignoring start'
                    ' request' % (vmname))
            else:
                self.__startVmCommand(vm)

                self.getLogger().debug(
                    '[vmware] VM [%s] started successfully' % (vmname))
        finally:
            self.__disconnect_vsphere()

        if tgt_host:
            node.parentnode = tgt_host

    def __getNodeNetworkOptions(self, node):\
            # pylint: disable=no-self-use
        # Check if there are any VLANs on this node
        networkOptionsList = []

        for dbHardwareProfileNetwork in \
                node.hardwareprofile.hardwareprofilenetworks:
            nodeNetwork = dbHardwareProfileNetwork.network

            nodeNetworkOptionString = nodeNetwork.getOptions()

            vmNetworkOptionsDict = {}

            if nodeNetworkOptionString:
                vmNetworkOptionsList = nodeNetworkOptionString.split(';')

                for vmNetworkOption in vmNetworkOptionsList:
                    key, value = vmNetworkOption.split('=')
                    vmNetworkOptionsDict[key] = value

                vmNetworkOptionsDict['portgroup'] = '%s/%s' % (
                    nodeNetwork.address, nodeNetwork.netmask)

                networkOptionsList.append(vmNetworkOptionsDict)

        return networkOptionsList

    def __getVlanConfiguration(self, node):
        # Find the VLAN settings in the list of node network options
        # dicts
        for nodeNetworkOptions in self.__getNodeNetworkOptions(node):
            if 'vlan' not in nodeNetworkOptions:
                continue

            return nodeNetworkOptions

        return None

    def __get_network_by_id(self, networks, id_):\
            # pylint: disable=no-self-use
        for network in networks:
            if int(network.id) == id_:
                break

        return None

    def __connectVlan(self, node, parentNode):
        nodeNetworkOptions = self.__getVlanConfiguration(node)

        if not nodeNetworkOptions:
            # No VLAN configuration found, bail out
            return

        portGroup = nodeNetworkOptions['portgroup']
        vlanId = nodeNetworkOptions['vlan']
        vlanParentNetworkId = int(nodeNetworkOptions['vlanparent'])

        parentNodeHardwareProfile = parentNode.hardwareprofile

        hypervisorNetwork = self.__get_network_by_id(
            parentNodeHardwareProfile.hardwareprofilenetworks.network,
            vlanParentNetworkId)

        if not hypervisorNetwork:
            return

        # hypervisorNetwork is the parent network of the VLAN
        # TODO: this needs to read from adapter-defaults-vmware.conf
        deviceName = "vSwitch%s" % (
            hypervisorNetwork.networkdevice.name[3:])

        # Now we need to make sure that portGroup is connected on
        # the hypervisor
        config = configparser.ConfigParser()

        cfgfile = os.path.join(
            self._cm.getKitConfigBase(), self.VLAN_DATA_FILE)

        config.read(cfgfile)

        # Check if the volume is currently connected
        if not config.has_section(parentNode.name):
            config.add_section(parentNode.name)

        if not config.has_option(parentNode.name, portGroup):
            # Hypervisor doesn't have bridge
            config.set(parentNode.name, portGroup, '1')

            # Create and connect bridge
            #TODO: Use real api
            self.__addPortGroupCommand(
                deviceName, portGroup, parentNode.name, vlanId)
        else:
            currentVal = config.get(parentNode.name, portGroup)

            config.set(
                parentNode.name, portGroup, str(int(currentVal) + 1))

        with open(self.VLAN_DATA_FILE, 'w') as fp:
            config.write(fp)

    def __disconnectVlan(self, node, parentNode):
        if not parentNode:
            return

        nodeNetworkOptions = self.__getVlanConfiguration(node)

        if nodeNetworkOptions is None:
            return

        portGroup = nodeNetworkOptions['portgroup']
        vlanParentNetworkId = int(nodeNetworkOptions['vlanparent'])

        parentNodeHardwareProfile = parentNode.hardwareprofile

        hypervisorNetwork = self.__get_network_by_id(
            parentNodeHardwareProfile.hardwareprofilenetworks.network,
            vlanParentNetworkId)

        if hypervisorNetwork is None:
            return

        # See if there are any other VMs still using this network
        config = configparser.ConfigParser()

        config.read(self.VLAN_DATA_FILE)

        # Get the reference counter, decrement and see if there are
        # any nodes left

        if not config.has_section(parentNode.name):
            raise Exception(
                'Config section not found: {}'.format(parentNode.name))

        if not config.has_option(parentNode.name, portGroup):
            raise Exception(
                'Config option missing: {}'.format(portGroup)
            )

        currentVal = config.getint(parentNode.name, portGroup)

        newVal = currentVal - 1

        if newVal > 0:
            # VLAN needs to stay connected, just decrement the
            # reference counter
            config.set(parentNode.name, portGroup, str(newVal))
        else:
            # Disconnect VLAN
            config.remove_option(parentNode.name, portGroup)

            # Create and connect bridge
            self.__removePortGroupCommand(portGroup, parentNode.name)

        with open(self.VLAN_DATA_FILE, 'w') as fp:
            config.write(fp)

    def __processDeletedDiskChanges(self, node, diskChanges):
        # Remove any disks that are no longer needed
        for removedDiskNumber in list(diskChanges['removed'].keys()):
            storageAdapter = \
                diskChanges['removed'][removedDiskNumber]['adapter']

            # device = diskChanges['removed'][removedDiskNumber]['device']

            if storageAdapter == 'default':
                self.sanApi.deleteDrive(node, removedDiskNumber)
            else:
                # Get the device path for removal
                #diskPath = 'phy:%s' % device
                self.sanApi.deleteDrive(node, removedDiskNumber)

    def __iter_added_disk_changes(self, diskChanges): \
            # pylint: disable=no-self-use
        """
        Returns a tuple of added disk number and added disk details
        """

        for addedDiskNumber, addedDisk in zip(
                itertools.count(0), [
                    diskChanges['added'][key]
                    for key in sorted(diskChanges['added'].keys())]):
            yield addedDiskNumber, addedDisk

    def __processAddedDiskChanges(self, node, diskChanges,
                                  bThinProvision=False):
        """
        Added any new disks to specified node
        """

        vmname = self.__getVmName(node)

        for unitNumber, addedDisk in \
                self.__iter_added_disk_changes(diskChanges):
            storageAdapter = addedDisk['adapter']
            size = addedDisk['size']
            sanVolume = None

            if storageAdapter != 'default':
                # Currently not supported
                self.getLogger().error(
                    '[vmware] Only default disks are supported for'
                    ' VMware Nodes (%s, %s, %s)' % (
                        vmname, storageAdapter, size))

            # Do physical disk create
            diskPath = '[%s] %s/%s-%s.vmdk' % (
                self.__dataStore, vmname, vmname, unitNumber)

            self.getLogger().info(
                '[vmware] Adding virtual disk: (%s, %s, %s)' % (
                    vmname, diskPath, size))

            self.__addFileBackedDiskCommand(
                vmname, size, self.__dataStore, diskPath, unitNumber,
                bThinProvision=bThinProvision)

            # Add placeholder to the storage subsystem so managed
            # drives get tracked
            self.sanApi.addDrive(
                node, storageAdapter, str(unitNumber + 1), size,
                sanVolume)

    def __getVmName(self, node):
        """
        Return VM name by first checking instance cache for hostname to VM
        name mapping, otherwise return node name verbatim
        """

        vmname = self.__instance_cache_get(node.name)

        return node.name if vmname is None else vmname

    def __stopVm(self, node, bHard=False):
        """
        Stop a vm

        Raises:
            CommandFailed
            VirtualMachineNotFound
        """

        vmname = self.__getVmName(node)

        server = self.__connect_vsphere()

        try:
            vm = self.__get_vm_by_name(server, vmname)

            if not vm.is_powered_on():
                self.getLogger().info(
                    '[vmware] VM [%s] not powered on; ignoring stop'
                    ' request' % (vmname))

                return

            # Stop the VM
            self.__stopVmCommand(vm, bHard)

            self.getLogger().debug('[vmware] VM [%s] stopped' % (vmname))
        finally:
            self.__disconnect_vsphere()

        self.__disconnectVlan(node, node.parentnode)

    def __suspendVm(self, vmname, saveFilePath=None): \
            # pylint: disable=unused-argument
        """Suspend a VM

        Raises:
            CommandFailed
        """

        server = self.__connect_vsphere()

        try:
            vm = self.__get_vm_by_name(server, vmname)

            if vm.is_powered_on():
                # Only attempt to suspend a VM that is powered on
                self.__suspendVmCommand(vm)
            else:
                vm_status = vm.get_status()

                if vm_status == 'SUSPENDED':
                    self.getLogger().info(
                        '[vmware] VM [%s] is already suspended.'
                        ' Nothing to do.')
                else:
                    raise CommandFailed(
                        'VM [%s] is not running (status: %s)' % (
                            vmname, vm_status))
        finally:
            self.__disconnect_vsphere()

    def __migrateCommand(self, nodeName, tgt_host): \
            # pylint: disable=no-self-use,unused-argument
        raise CommandFailed('Migration currently not supported')

    def __get_datacenter_mor(self, server, datacenter_name): \
            # pylint: disable=no-self-use
        """
        Returns a MOR for the specified datacenter

        Raises:
            CommandFailed
        """

        datacenter_mor = [
            k for k, v in list(server.get_datacenters().items())
            if v == datacenter_name
        ]

        if not datacenter_mor:
            errmsg = 'Datacenter [%s] not found' % (datacenter_name)

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

        return datacenter_mor[0]

    def __deleteVmCommand(self, vmname):
        """
        Raises:
            VirtualMachineNotFound
            CommandFailed
        """

        server = self.__connect_vsphere()

        try:
            vm = self.__get_vm_by_name(server, vmname)

            # Invoke Destroy_Task
            request = VI.Destroy_TaskRequestMsg()
            _this = request.new__this(vm._mor)
            _this.set_attribute_type(vm._mor.get_attribute_type())
            request.set_element__this(_this)
            ret = server._proxy.Destroy_Task(request)._returnval

            # Wait for the task to finish
            task = pysphere.VITask(ret, server)

            status = task.wait_for_state(
                [task.STATE_SUCCESS, task.STATE_ERROR])

            if status != task.STATE_SUCCESS:
                raise CommandFailed(
                    'Error destroying VM [%s] (err=%s)' % (
                        vm.get_property('name'),
                        task.get_error_message()))
        finally:
            self.__disconnect_vsphere()

    def __deleteSnapshotCommand(self, vm, snapshot_name='Tortuga'): \
            # pylint: disable=no-self-use
        """
        Raises:
            CommandFailed
        """

        try:
            vm.delete_named_snapshot(snapshot_name, remove_children=True)
        except pysphere.VIException as ex:
            if ex.fault != pysphere.FaultTypes.OBJECT_NOT_FOUND:
                raise CommandFailed(
                    'Unable to delete snapshot [%s] on VM [%s]:'
                    ' (err=%s)' % (snapshot_name, vm.get_property('name'),
                                   ex.message))

    def __createSnapshotCommand(self, vm, snapshot_name='Tortuga'): \
            # pylint: disable=no-self-use
        """
        Raises:
            CommandFailed
        """

        try:
            vm.create_snapshot(snapshot_name)
        except pysphere.VIException as ex:
            raise CommandFailed(
                'Error creating snapshot on VM [%s] (err=%s)' % (
                    vm.get_property('name'), ex.message))

    def __revertSnapshotCommand(self, vm, snapshot_name='Tortuga'): \
            # pylint: disable=no-self-use
        """
        Raises:
            CommandFailed
        """

        try:
            vm.revert_to_named_snapshot(snapshot_name)
        except pysphere.VIException as ex:
            errmsg = 'Error reverting snapshot on VM [%s] (err=%s)' % (
                vm.get_property('name'), ex.message)

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

    def __startVmCommand(self, vm):     # pylint: disable=no-self-use
        """
        Raises:
            CommandFailed
        """

        try:
            vm.power_on()
        except pysphere.resources.vi_exception.VIException as ex:
            errmsg = 'Unable to start VM [%s]: %s [%s]' % (
                vm.get_property('name'), ex.fault, ex.message)

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

    def __connect_vsphere(self, vsphere_host_name=None):
        if self._server is None:
            server = pysphere.VIServer()

            server.connect(
                vsphere_host_name
                if vsphere_host_name else self.__vCenterHostName,
                self.__vCenterUserName,
                self.__vCenterPassword)

            self._server = server
        else:
            self.nConnectionCount += 1

            self.getLogger().debug(
                '[vmware] connection count: %d' % (self.nConnectionCount))

        return self._server

    def __disconnect_vsphere(self):
        if self.nConnectionCount > 0:
            self.nConnectionCount -= 1

        if self.nConnectionCount == 0:
            self._server.disconnect()

            self._server = None

    def __stopVmCommand(self, vm, hard=False): \
            # pylint: disable=no-self-use
        """
        Raises:
            CommandFailed
        """

        try:
            if hard:
                vm.power_off()
            else:
                try:
                    vm.shutdown_guest()
                except pysphere.resources.vi_exception.VIException as ex:
                    if ex.fault != 'ToolsUnavailableFault':
                        raise

                    vm.power_off()
        except pysphere.resources.vi_exception.VIException as ex:
            errmsg = 'Error stopping VM [%s]: %s [%s]' % (
                vm.get_property('name'), ex.fault, ex.message)

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

    def __resetVmCommand(self, vm):
        """
        Raises:
            CommandFailed
        """

        self.getLogger().debug(
            '[vmware] Resetting VM [%s]' % (vm.get_property('name')))

        try:
            vm.reset()
        except pysphere.resources.vi_exception.VIException as ex:
            errmsg = 'Unable to reset VM [%s]: %s [%s]' % (
                vm.get_property('name'), ex.fault, ex.message)

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

    def __suspendVmCommand(self, vm):   # pylint: disable=no-self-use
        """
        Raises:
            VirtualMachineNotFound
            CommandFailed
        """

        try:
            vm.suspend()
        except pysphere.resources.vi_exception.VIException as ex:
            errmsg = 'Unable to suspend VM [%s]: %s [%s]' % (
                vm.get_property('name'), ex.fault, ex.message)

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

    def __get_host_mor(self, server, host_name): \
            # pylint: disable=no-self-use
        host_mor = [
            k for k, v in list(server.get_hosts().items()) if v == host_name
        ]

        if not host_mor:
            # Unable to find hypervisor
            errmsg = 'Hypervisor [%s] not found' % (host_name)

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

        return host_mor[0]

    def __getVmList(self, hypName):
        """
        Raises:
            CommandFailed
        """

        server = self.__connect_vsphere()

        try:
            host_mor = self.__get_host_mor(server, hypName)

            vms = server._get_managed_objects(
                MORTypes.VirtualMachine, from_mor=host_mor)
        finally:
            self.__disconnect_vsphere()

        return list(vms.values())

    def __addFileBackedDiskCommand(self, vmname, disksize, datastore,
                                   filename, unitnumber,
                                   bThinProvision=False): \
            # pylint: disable=unused-argument
        """
        Raises:
            CommandFailed
            VirtualMachineNotFound
        """

        s = self.__connect_vsphere()

        try:
            vm = self.__get_vm_by_name(s, vmname)

            request = VI.ReconfigVM_TaskRequestMsg()
            _this = request.new__this(vm._mor)
            _this.set_attribute_type(vm._mor.get_attribute_type())

            request.set_element__this(_this)

            spec = request.new_spec()

            dc = spec.new_deviceChange()
            dc.Operation = 'add'
            dc.FileOperation = 'create'

            hd = VI.ns0.VirtualDisk_Def('hd').pyclass()
            hd.Key = -100
            hd.UnitNumber = unitnumber
            hd.CapacityInKB = disksize * 1024
            hd.ControllerKey = 1000

            backing = VI.ns0.\
                VirtualDiskFlatVer2BackingInfo_Def("backing").pyclass()
            backing.FileName = "[%s]" % (datastore)
            backing.DiskMode = "persistent"
            backing.Split = False
            backing.WriteThrough = False
            backing.ThinProvisioned = bThinProvision
            backing.EagerlyScrub = False
            hd.Backing = backing

            dc.Device = hd

            spec.DeviceChange = [dc]
            request.Spec = spec

            task = s._proxy.ReconfigVM_Task(request)._returnval
            vi_task = VITask(task, s)

            #Wait for task to finis
            status = vi_task.wait_for_state(
                [vi_task.STATE_SUCCESS, vi_task.STATE_ERROR])

            if status != vi_task.STATE_SUCCESS:
                errmsg = 'Error adding virtual disk to VM [%s] (err=%s)' % (
                    vmname, vi_task.get_error_message())

                self.getLogger().error('[vmware] ' + errmsg)

                raise CommandFailed(errmsg)
        finally:
            self.__disconnect_vsphere()

    def __migrateVm(self, node, remainingNodeList, liveMigrate=True):
        """
        Migrate a vm

        Raises:
            SoftwareProfileNotFound
        """

        nodeName = node.name

        softwareProfile = node.hardwareprofile.hypervisor

        tgt_host = self.__getDedicatedServer(
            softwareProfile, remainingNodeList=remainingNodeList)

        self.getLogger().debug(
            '[vmware] Migrating node [%s] to hypervisor [%s]' % (
                node, tgt_host))

        if liveMigrate:
            try:
                self.__connectVlan(node, tgt_host)

                # Connect drives to new hypervisor
                self.__connectDisks(
                    node, ['unchanged'], tgt_host.name)

                # Actually Migrate the node
                self.__migrateCommand(nodeName, tgt_host.name)

                # Remove the vlan on the old hypervisor
                for nodeNetworkOptions in self.__getNodeNetworkOptions(node):
                    if 'vlan' not in nodeNetworkOptions:
                        continue

                    # Check if this is the last node on the hypervisor and
                    # remove VLAN if it is
                    # self.__disconnectVlan(
                    #     nodeNetworkOptions['portgroup'],
                    #     nodeNetworkOptions['vlan'],
                    #     nodeNetworkOptions['vlanparent'],
                    #     originalParentNodeName)

                # Disconnect drives from old hypervisor
                # self.__disconnectDisks(
                #     node, ['unchanged'], None, originalParentNodeName)

            except CommandFailed as ex:
                errmsg = 'Error performing live migration: %s' % (ex)

                self.getLogger().error('[vmware] %s' % (errmsg))

                raise CommandFailed(errmsg)
        else:
            # If we're not live migrating, we're going to just stop the VM
            # and restart it on the new hypervisor

            # First shutdown the machine
            self.__stopVm(node)

            # Start the machine back up on the new hypervisor
            self.__startVm(node, remainingNodeList)

        node.parentnode = tgt_host

    def __destroyVm(self, node):
        """
        Destroy a vm

        Raises:
            CommandFailed
        """

        # Get disk changes
        diskChanges = self.sanApi.discoverStorageChanges(node, True)

        # Process deletes
        self.__processDeletedDiskChanges(node, diskChanges)

        vmname = self.__getVmName(node)

        # Delete the actual VM
        try:
            self.__deleteVmCommand(vmname)
        except VirtualMachineNotFound:
            # VM does not exist, so we have nothing to do
            pass

    def __addDiskToVm(self, node, diskPath, position=-1):
        """
        Add a disk to a node

        TODO: Currently a no-op
        """

    def abort(self):    # pylint: disable=no-self-use
        self.looping = False

    def __getMacAddress(self):      # pylint: disable=no-self-use
        validRandomMac = False
        while not validRandomMac:
            h = hex(random.getrandbits(32)).lower()
            if len(h) > 8:
                validRandomMac = True

        # First byte must be between 00 and 3f
        h1 = '%02x' % (int('0x%s' % h[-3:-1], 16) % 0x3f)
        h2 = h[-5:-3]
        h3 = h[-7:-5]
        return '00:50:56:%s:%s:%s' % (h1, h2, h3)

    def __mapToVmwareGuestOS(self, osInfo): \
            # pylint: disable=no-self-use
        if osInfo.family.name == 'rhel':
            if osInfo.arch == 'x86_64':
                guestOS = 'rhel%s-64' % (osInfo.family.version)
        else:
            # Fallback to sane default
            guestOS = 'rhel5-64'

        return guestOS

    def __get_cluster_mor_by_name(self, s, dcmor, clustername): \
            # pylint: disable=no-self-use
        """
        Raises:
            UnsupportedOperation
        """

        clusters = [
            k for k, v in s.get_clusters(from_mor=dcmor).items()
            if v == clustername
        ]

        if not clusters:
            errmsg = 'VMware vSphere cluster [%s] not found' % (clustername)

            self.getLogger().error('[vmware] ' + errmsg)

            raise UnsupportedOperation(errmsg)

        return clusters[0]

    def __get_host_mor_by_hostname(self, s, dcmor, hostname): \
            # pylint: disable=no-self-use
        """
        Raises:
            UnsupportedOperation
        """

        hostmors = [
            k for k, v in list(s.get_hosts(from_mor=dcmor).items())
            if v == hostname
        ]

        if not hostmors:
            errmsg = \
                'Hypervisor [%s] is not part of the datacenter' % (hostname)

            self.getLogger().error('[vmware] ' + errmsg)

            raise UnsupportedOperation(errmsg)

        return hostmors[0]

    def __get_random_host_mor(self, s, source_mor):
        """
        Given a source MOR, return a randomly chosen host

        Raises:
            UnsupportedOperation
        """

        # Get list of available hypervisor hosts
        hypervisor_hosts = list(s.get_hosts(from_mor=source_mor).keys())

        if not hypervisor_hosts:
            errmsg = 'Cluster [%s] contains no valid hypervisors' % (
                self.__vCenterCluster)

            self.getLogger().error('[vmware] ' + errmsg)

            raise UnsupportedOperation(errmsg)

        # Randomly choose a hypervisor from those available
        hostmor = random.choice(hypervisor_hosts)

        return hostmor

    def __get_resource_pool_mor_by_name(self, s, poolname): \
            # pylint: disable=no-self-use
        """
        Raises:
            CommandFailed
        """

        matching_rpmor = [
            k for k, v in s.get_resource_pools().items()
            if v == poolname
        ]

        if not matching_rpmor:
            errmsg = 'Resource pool path [%s] not found' % (poolname)

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

        rpmor = matching_rpmor[0]

        return rpmor

    def __get_portgroup_mor_for_network(self, network_name, dvpg_mors): \
            # pylint: disable=no-self-use
        dvpg_mor = None

        if not dvpg_mors:
            return dvpg_mor

        for dvpg in dvpg_mors:
            if dvpg_mor:
                break

            for p in dvpg.PropSet:
                if p.Name == "name" and p.Val == network_name:
                    dvpg_mor = dvpg

                if dvpg_mor:
                    break

        return dvpg_mor

    def __get_dvswitch_mor_for_portgroup_key(self, s, portgroupKey,
                                             dvswitch_mors): \
            # pylint: disable=no-self-use
        """Given the portgroup key, return the MOR for the distributed
        vSwitch
        """

        dvswitch_mor = None

        # Get the appropriate dvswitches managed object
        for dvswitch in dvswitch_mors:
            if dvswitch_mor:
                break

            for p in dvswitch.PropSet:
                if p.Name != "portgroup":
                    continue

                pg_mors = p.Val.ManagedObjectReference

                for pg_mor in pg_mors:
                    if dvswitch_mor:
                        break

                    key_mor = s._get_object_properties(
                        pg_mor, property_names=['key'])

                    for key in key_mor.PropSet:
                        if key.Val == portgroupKey:
                            dvswitch_mor = dvswitch

        return dvswitch_mor

    def __get_mor_property(self, mor, prop_name): \
            # pylint: disable=no-self-use
        result = None

        for p in mor.PropSet:
            if p.Name == prop_name:
                result = p.Val
                break

        return result

    def __get_folder_mor(self, s, dcmor, destinationPath):
        """
        Look up the MOR for the specified path

        Raises:
            CommandFailed
        """

        # Something is fishy here... if the 'from_node' argument is omitted
        # from the call to _retrieve_properties_traversal(), it seemingly
        # omits the Datacenter level so the results are "Datacenters/vm/...".
        #
        # I think the result should be "Datacenters/<name of
        # datacenter>/..."
        #
        # For this reason, we pass in the MOR of the datacenter (dcmor)
        # we're operating against to omit this folder parsing weirdness.

        folders = s._retrieve_properties_traversal(
            property_names=['name', 'parent'], from_node=dcmor,
            obj_type='Folder')

        destination_mor = dcmor

        # Additively iterate over the destination folder path to determine
        # MOR of final path component. Skip over the first component,
        # which is the datacenter name.
        for path_component in destinationPath.lstrip('/').split('/')[1:]:
            for folder in folders:
                if folder.PropSet[0].Val == path_component and \
                   folder.PropSet[1].Val == destination_mor:
                    # Found matching MOR for path component
                    destination_mor = folder.Obj

                    break
            else:
                # No match found, break out for iteration
                destination_mor = None

                break

        if destination_mor is None:
            errmsg = 'Folder path [%s] is invalid/inaccessible' % (
                destinationPath)

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

        return destination_mor

    def __get_compute_resource_mor_by_host_mor(self, crmors, hostmor): \
            # pylint: disable=no-self-use
        crmor = None

        for cr in crmors:
            if crmor:
                break

            for p in cr.PropSet:
                if p.Name == "host":
                    for h in p.Val.get_element_ManagedObjectReference():
                        if h == hostmor:
                            crmor = cr.Obj
                            break
                    if crmor:
                        break

        return crmor

    def __createVm(self, addNodeRequest, node):
        """
        Raises:
            CommandFailed
            UnsupportedOperation
        """

        hypervisorNode = addNodeRequest['parentNode']

        configDict = addNodeRequest['configDict']

        nicspec = self.__getBridgeNameMap(node.nics, hypervisorNode)

        vmname = self.__getVmName(node)

        self.getLogger().debug(
            '[vmware] __createVm(): vmname=[%s]' % (vmname))

        hostname = hypervisorNode.name if hypervisorNode else None
        memorysize = int(configDict['ramsize'])
        cpucount = int(configDict['ncpus'])
        guestosid = "rhel6_64Guest"

        #OPTIONAL PARAMETERS
        datastorename = self.__dataStore

        # Connect to vCenter
        s = self.__connect_vsphere()

        # Get the MOR for the defined datacenter
        dcmor = self.__get_datacenter_mor(s, self.__vCenterDataCenter)

        # If cluster is defined, find cluster MOR within datacenter.
        c_mor = self.\
            __get_cluster_mor_by_name(s, dcmor, self.__vCenterCluster) \
            if self.__vCenterCluster is not None else None

        dcprops = VIProperty(s, dcmor)

        hfmor = dcprops.hostFolder._obj

        # Get the MOR of the destination folder, otherwise use the MOR of
        # the datacenter itself.
        destination_mor = self.__get_folder_mor(
            s, dcmor, self.__destinationPath) \
            if self.__destinationPath else dcprops.vmFolder._obj

        # Get ComputeResource MORs used to determine the host where this
        # VM will be created.
        crmors = s._retrieve_properties_traversal(
            property_names=['name', 'host'], from_node=hfmor,
            obj_type='ComputeResource')

        # If the hypervisor host name is not specified, choose a random
        # host from the cluster, if defined, otherwise from the datacenter.
        if hostname is None:
            # If a cluster is defined, use only hosts within that cluster,
            # otherwise use any host in the datacenter.
            source_mor = c_mor if c_mor is not None else dcmor

            hostmor = self.__get_random_host_mor(s, source_mor)
        else:
            # Find MOR for specified host name
            hostmor = self.__get_host_mor_by_hostname(s, dcmor, hostname)

        crmor = \
            self.__get_compute_resource_mor_by_host_mor(crmors, hostmor)

        crprops = VIProperty(s, crmor)

        # Get resource pool MOR from the configured resource pool,
        # otherwise from the default pool
        rpmor = self.__get_resource_pool_mor_by_name(s, self.__vCenterPool) \
            if self.__vCenterPool else crprops.resourcePool._obj

        # Create VM configuration

        # Get config target
        request = VI.QueryConfigTargetRequestMsg()
        _this = request.new__this(crprops.environmentBrowser._obj)
        _this.set_attribute_type(
            crprops.environmentBrowser._obj.get_attribute_type())
        request.set_element__this(_this)
        h = request.new_host(hostmor)
        h.set_attribute_type(hostmor.get_attribute_type())
        request.set_element_host(h)
        config_target = s._proxy.QueryConfigTarget(request)._returnval

        # Get default devices
        request = VI.QueryConfigOptionRequestMsg()
        _this = request.new__this(crprops.environmentBrowser._obj)
        _this.set_attribute_type(
            crprops.environmentBrowser._obj.get_attribute_type())
        request.set_element__this(_this)
        h = request.new_host(hostmor)
        h.set_attribute_type(hostmor.get_attribute_type())
        request.set_element_host(h)
        # config_option = s._proxy.QueryConfigOption(request)._returnval
        # defaul_devs = config_option.DefaultDevice

        # Get datastore
        ds = None
        for d in config_target.Datastore:
            if (d.Datastore.Accessible and
                    (datastorename and
                     d.Datastore.Name == datastorename) or
                    (not datastorename)):
                ds = d.Datastore.Datastore
                datastorename = d.Datastore.Name
                break

        if not ds:
            errmsg = 'Datastore [%s] not found' % (datastorename)

            self.getLogger().error('[vmware]' + errmsg)

            raise CommandFailed(errmsg)

        volume_name = "[%s]" % (datastorename)

        # Add parameters to the create VM task
        create_vm_request = VI.CreateVM_TaskRequestMsg()

        config = create_vm_request.new_config()
        vmfiles = config.new_files()
        vmfiles.set_element_vmPathName(volume_name)
        config.set_element_files(vmfiles)
        config.set_element_name(vmname)
        # config.set_element_annotation(annotation)
        config.set_element_memoryMB(memorysize)
        config.set_element_numCPUs(cpucount)
        config.set_element_guestId(guestosid)

        # Set the Virtual Hardware Version to user-configured value,
        # otherwise use the default (currently 'vmx-10')
        if configDict['hardware_version']:
            config.set_element_version(configDict['hardware_version'])

        devices = []

        # Add a SCSI controller
        disk_ctrl_key = 1
        scsi_ctrl_spec = config.new_deviceChange()
        scsi_ctrl_spec.set_element_operation('add')
        scsi_ctrl = \
            VI.ns0.VirtualLsiLogicController_Def("scsi_ctrl").pyclass()
        scsi_ctrl.set_element_busNumber(0)
        scsi_ctrl.set_element_key(disk_ctrl_key)
        scsi_ctrl.set_element_sharedBus("noSharing")
        scsi_ctrl_spec.set_element_device(scsi_ctrl)
        devices.append(scsi_ctrl_spec)

        nfmor = dcprops.networkFolder._obj

        # Get distributed virtual portgroup MORs
        dvpg_mors = s._retrieve_properties_traversal(
            property_names=['name', 'key'], from_node=nfmor,
            obj_type='DistributedVirtualPortgroup')

        dvswitch_mors = s._retrieve_properties_traversal(
            property_names=['uuid', 'portgroup'], from_node=nfmor,
            obj_type='DistributedVirtualSwitch')

        # Iterate over NICs in order
        for nic_details in nicspec:
            nic_spec = config.new_deviceChange()
            nic_spec.set_element_operation("add")
            nic_ctlr = VI.ns0.VirtualVmxnet3_Def('nic_ctlr').pyclass()

            # Determine whether this NIC is on a distributed virtual switch
            # or a standard NIC.

            # Get portgroup MOR for specified network
            dvpg_mor = self.__get_portgroup_mor_for_network(
                nic_details['networkName'], dvpg_mors)

            network_type = 'dvs' if dvpg_mor else 'standard'

            # Get the portgroup key
            portgroupKey = None
            if dvpg_mor:
                # Iterate over distributed virtual portgroup properties to
                # determine the UUID.

                portgroupKey = self.__get_mor_property(dvpg_mor, 'key')

                # Get distributed vSwitch UUID and portgroup properties
                dvswitch_mor = self.__get_dvswitch_mor_for_portgroup_key(
                    s, portgroupKey, dvswitch_mors)

                # Get UUID of distributed vSwitch
                dvswitch_uuid = self.__get_mor_property(dvswitch_mor, 'uuid')

            if network_type == 'standard':
                nic_backing = VI.ns0.\
                    VirtualEthernetCardNetworkBackingInfo_Def(
                        "nic_backing").pyclass()

                nic_backing.\
                    set_element_deviceName(nic_details['networkName'])
            elif network_type == 'dvs':
                nic_backing_port = VI.ns0.\
                    DistributedVirtualSwitchPortConnection_Def(
                        "nic_backing_port").pyclass()

                nic_backing_port.set_element_switchUuid(dvswitch_uuid)
                nic_backing_port.set_element_portgroupKey(portgroupKey)

                nic_backing = VI.ns0.\
                    VirtualEthernetCardDistributedVirtualPortBackingInfo_Def(
                        "nic_backing").pyclass()

                nic_backing.set_element_port(nic_backing_port)

            nic_ctlr.set_element_addressType("Manual")
            nic_ctlr.set_element_macAddress(nic_details['mac'])
            nic_ctlr.set_element_backing(nic_backing)
            nic_ctlr.set_element_key(4)
            nic_spec.set_element_device(nic_ctlr)
            devices.append(nic_spec)

        config.set_element_deviceChange(devices)
        create_vm_request.set_element_config(config)

        folder_mor = create_vm_request.new__this(destination_mor)

        folder_mor.set_attribute_type(destination_mor.get_attribute_type())

        create_vm_request.set_element__this(folder_mor)

        rp_mor = create_vm_request.new_pool(rpmor)
        rp_mor.set_attribute_type(rpmor.get_attribute_type())
        create_vm_request.set_element_pool(rp_mor)

        host_mor = create_vm_request.new_host(hostmor)
        host_mor.set_attribute_type(hostmor.get_attribute_type())
        create_vm_request.set_element_host(host_mor)

        # Create the VM
        taskmor = s._proxy.CreateVM_Task(create_vm_request)._returnval

        task = VITask(taskmor, s)
        task.wait_for_state([task.STATE_SUCCESS, task.STATE_ERROR])
        if task.get_state() == task.STATE_ERROR:
            errmsg = "Error creating vm: %s" % task.get_error_message()

            self.getLogger().debug('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

        # Reconfigure the newly created VM to set the boot order
        vm = s.get_vm_by_name(vmname)

        vm.set_extra_config({
            'bios.bootDeviceClasses': 'allow:net',
        })

    def __validateHypervisor(self, hardwareProfile):
        """
        Raises:
            CommandFailed if no hypervisors in Installed state are
            available
        """

        if not hardwareProfile.hypervisor:
            return

        nodeList = hardwareProfile.hypervisor.nodes

        hypervisors = [n for n in nodeList if n.state == 'Installed']

        if not hypervisors:
            errmsg = ('No hypervisor(s) found in Installed state.'
                      ' VMs not added.')

            self.getLogger().error('[vmware] %s' % (errmsg))

            raise CommandFailed(errmsg)

        self.getLogger().debug(
            '[vmware] Found available hypervisor [%s]' % (hypervisors[0]))

    def __loadConfiguration(self, cfgfile):
        """
        Raises:
            CommandFailed
        """

        if not os.path.exists(cfgfile):
            errmsg = ('VMware resource adapter configuration file [%s]'
                      ' not found.')

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

        config = configparser.ConfigParser()

        config.read(cfgfile)

        # Ensure at least the '[vcenter]' section exists
        if not config.has_section('vcenter'):
            raise CommandFailed(
                'VMware vSphere vCenter is not configured. [vcenter]'
                ' section missing from configuration file [%s]' % (
                    cfgfile))

        # Ensure VMware vSphere vCenter server hostname is properly defined
        if not config.has_option('vcenter', 'hostname'):
            raise CommandFailed(
                'VMware vSphere vCenter server hostname not specified in'
                ' configuration')

        self.__vCenterHostName = config.get('vcenter', 'hostname')

        if not self.__vCenterHostName:
            errmsg = ('VMware vSphere vCenter server hostname cannot be'
                      ' blank')

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

        if not config.has_option('vcenter', 'username') or \
           not config.has_option('vcenter', 'password'):
            errmsg = ('VMware vSphere username and password must be'
                      ' specified in [%s]' % (cfgfile))

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

        self.__vCenterUserName = config.get('vcenter', 'username')
        self.__vCenterPassword = config.get('vcenter', 'password')

        if config.has_option('vcenter', 'datacenter'):
            # This is (now) an optional setting, only if the path is
            # specified
            self.__vCenterDataCenter = config.get(
                'vcenter', 'datacenter')

        # Destination folder
        if config.has_option('vcenter', 'path'):
            value = config.get('vcenter', 'path')

            if not value:
                value = None

            self.__destinationPath = value

        if self.__vCenterDataCenter is None and \
           self.__destinationPath is None:
            errmsg = ('At least one of: vSphere datacenter or path'
                      '*must* be configured in [%s]' % (cfgfile))

            self.getLogger().error('[vmware] ' + errmsg)

            raise CommandFailed(errmsg)

        # Sanity check and a log warning for malformed path
        if self.__destinationPath and \
                not self.__destinationPath.startswith('/'):
            self.getLogger().warning(
                '[vmware] path [%s] should have a leading forward slash' % (
                    self.__destinationpath))

            self.__destinationPath = '/' + self.__destinationPath

        # If both datacenter and folder path is specified, ensure the
        # settings are consistent.
        if self.__vCenterDataCenter is not None and \
           self.__destinationPath is not None:
            if not self.__destinationPath.startswith(
                    '/%s/' % (self.__vCenterDataCenter)):
                errmsg = ('Mismatch between datacenter [%s] and path [%s]'
                          '. First element of path must be the'
                          ' datacenter' % (
                              self.__vCenterDataCenter,
                              self.__destinationPath))

                self.getLogger().error('[vmware] ' + errmsg)

                raise CommandFailed(errmsg)

        if self.__destinationPath and not self.__vCenterDataCenter:
            # Remove the leading forward slash and split into component
            # pieces.
            path_components = self.__destinationPath.lstrip('/').split('/')

            # The first component of the path is the datacenter name
            self.__vCenterDataCenter = path_components[0]

        if config.has_section('datastore') and \
           config.has_option('datastore', 'label'):
            self.__dataStore = config.get('datastore', 'label')

            if not self.__dataStore:
                errmsg = 'Datastore label cannot be blank'

                self.getLogger().error('[vmware] ' + errmsg)

                raise CommandFailed(errmsg)

        if config.has_option('vcenter', 'pool'):
            self.__vCenterPool = config.get('vcenter', 'pool')

            if not self.__vCenterPool:
                errmsg = 'Resource pool cannot be blank'

                self.getLogger().error('[vmware] ' + errmsg)

                raise CommandFailed(errmsg)

        if config.has_option('vcenter', 'cluster'):
            self.__vCenterCluster = config.get('vcenter', 'cluster')

            if not self.__vCenterCluster:
                errmsg = 'Cluster cannot be blank'

                self.getLogger().error('[vmware] ' + errmsg)

                raise CommandFailed(errmsg)

    def __config_section_to_dict(self, cfgparser, section_name): \
            # pylint: disable=no-self-use
        result = {}

        for key, value in cfgparser.items(section_name):
            result[key] = value

        return result

    def __load_resource_adapter_config(self, cfg_filename,
                                       dbHardwareProfile,
                                       dbSoftwareProfile):
        """
        "Flatten" the resource adapter configuration such that defaults are
        applied and hardware/software profile sections are processed in
        correct order.

        The 'softwareProfileOverride' attribute determines whether any
        possible software profile overrides are read.
        """

        cfg = configparser.ConfigParser()

        if not os.path.exists(cfg_filename):
            return cfg

        cfg.read(cfg_filename)

        # Load default section first
        defaultConfig = \
            self.__config_section_to_dict(cfg, 'resource-adapter')

        hwp_config = {}

        # Load hardware profile specific section
        hwp_section_name = dbHardwareProfile.name

        if cfg.has_section(hwp_section_name):
            hwp_config = \
                self.__config_section_to_dict(cfg, hwp_section_name)

        # Attempt to process software profile overrides
        swp_config = {}

        swp_section_name = 'swp:%s' % (dbSoftwareProfile.name)

        if cfg.has_section(swp_section_name):
            if dbHardwareProfile.softwareOverrideAllowed:
                swp_config = \
                    self.__config_section_to_dict(cfg, swp_section_name)
            else:
                # Hardware profile does not allow software profile settings
                # to override hardware profile settings.
                self.getLogger().\
                    warning('[vmware] Section [%s] exists for software'
                            ' profile, but software profile override is'
                            ' disabled.' % (dbSoftwareProfile.name))

        # Defaults + hardware profile settings + software profile settings
        result = dict(list(defaultConfig.items()) +
                      list(hwp_config.items()) +
                      list(swp_config.items()))

        return result

    def _getResourceAdapterConfig(self, dbHardwareProfile,
                                  dbSoftwareProfile):
        """
        Raises:
            InvalidArgument
        """

        cfgfile = os.path.join(
            self._cm.getKitConfigBase(), 'adapter-defaults-vmware.conf')

        configDict = self.\
            __load_resource_adapter_config(cfgfile, dbHardwareProfile,
                                           dbSoftwareProfile)

        # Apply sane defaults if these settings are not provided.
        if 'ramsize' not in configDict:
            configDict['ramsize'] = self.DEFAULT_RAM_MB

        if 'ncpus' not in configDict:
            configDict['ncpus'] = self.DEFAULT_N_CPUS

        # TODO: this is currently unused
        if 'disksize' not in configDict:
            configDict['disksize'] = self.DEFAULT_DISK_GB * 1024

        configDict['thin_provision'] = \
            configDict['thin_provision'].lower() == 'true' \
            if 'thin_provision' in configDict else False

        configDict['hardware_version'] = \
            configDict['hardware_version'] \
            if 'hardware_version' in configDict else None

        return configDict

    def start(self, addNodesRequest, dbSession, dbHardwareProfile,
              dbSoftwareProfile=None):
        """
        Raises:
            InvalidArgument
            NicNotFound
            CommandFailed
        """

        self.getLogger().debug(
            '[vmware] start(): addNodesRequest=[%s], dbSession=[%s], '
            'dbHardwareProfile=[%s], dbSoftwareProfile=[%s]' % (
                addNodesRequest, dbSession, dbHardwareProfile,
                dbSoftwareProfile))

        if not dbSoftwareProfile:
            raise CommandFailed(
                "Software profile must be provided when adding nodes"
                " to this hardware profile")

        # Obtain the configuration dict for overriding parameters. Note:
        # this does not use the default configuration parser.

        configDict = self.\
            _getResourceAdapterConfig(dbHardwareProfile, dbSoftwareProfile)

        # if resourceAdapterConfig:
        #     configDict = dict(
        #         configDict.items() + resourceAdapterConfig.items())

        if 'count' not in addNodesRequest or addNodesRequest['count'] < 1:
            raise InvalidArgument('node count must be greater than zero')

        # Find a potential parent node. This only makes sense if the user
        # has defined the VMware hypervisors within Tortuga. Default
        # behaviour is to randomly select a hypervisor from available
        # hypervisors.
        tmpHypervisorNode = self.__getHypervisor(dbHardwareProfile)

        newNodes = []

        nodeDetails = addNodesRequest['nodeDetails'] \
            if 'nodeDetails' in addNodesRequest else []

        addNodeRequest = {
            'hardwareprofile': dbHardwareProfile,
            'softwareprofile': dbSoftwareProfile,
            'configDict': configDict,
        }

        addNodeRequest['from_template'] = \
            configDict['template'] is not None \
            if 'template' in configDict else False

        for _, nodeDetail in itertools.zip_longest(
                range(addNodesRequest['count']), nodeDetails,
                fillvalue={}):
            addNodeRequest['nodeDetail'] = nodeDetail

            addNodeRequest['parentNode'] = \
                self.__get_hypervisor_node(tmpHypervisorNode)

            if not addNodeRequest['from_template']:
                node = self.__create_new_node(addNodeRequest, newNodes,
                                              dbSession)
            else:
                node = self.__create_node_from_template(addNodeRequest,
                                                        newNodes,
                                                        dbSession)

            dbSession.add(node)

            newNodes.append(node)

            nics = [tmpnic for tmpnic in node.nics
                    if tmpnic.network.type == 'provision']

            self._pre_add_host(node.name,
                               dbHardwareProfile.name,
                               dbSoftwareProfile.name,
                               nics[0].ip)

            # Create DHCP/PXE configuration
            if not addNodeRequest['from_template']:
                self.writeLocalBootConfiguration(
                    node, dbHardwareProfile, dbSoftwareProfile)

        # Return list of Nodes objects
        return newNodes

    def __get_hypervisor_node(self, tmpHypervisorNode):
        """
        Find a hypervisor for the new node
        """

        parentNode = self.__getDedicatedServer(
            tmpHypervisorNode.softwareprofile) \
            if tmpHypervisorNode and \
            tmpHypervisorNode.softwareprofile else None

        if not parentNode:
            return None

        self.getLogger().debug(
            '[vmware] __get_hypervisor_node(): using hypervisor [%s]'
            ' for new node' % (parentNode.name))

        return parentNode

    def __create_nic_dict(self, index, nic_def):
        """
        Extract values from nic_def. Generate unique MAC address as
        necessary.

        This method is required to process the nic details that might be
        passed in through an input MAC address file, for example.
        """

        nicDict = {
            'device': index,
        }

        # MAC address always has to be defined
        nicDict['mac'] = \
            nic_def['mac'] if 'mac' in nic_def else self.__getMacAddress()

        # IP address is optional
        if 'ip' in nic_def:
            nicDict['ip'] = nic_def['ip']

        return nicDict

    def __generate_nics(self, nodeDetail, dbHardwareProfile):
        nics = []

        nic_count = 0

        # Iterate over networks in the hardware profile, creating NIC
        # entries in the add-node request.
        for nic_def, _ in itertools.zip_longest(
                nodeDetail['nics'] if 'nics' in nodeDetail else [],
                dbHardwareProfile.hardwareprofilenetworks,
                fillvalue={}):

            nics.append(self.__create_nic_dict(nic_count, nic_def))

            nic_count += 1

        return nics

    def __create_new_node(self, addNodeRequest, newNodes, dbSession):
        nodeDetail = addNodeRequest['nodeDetail']

        if 'name' in nodeDetail:
            # If node name is predefined, include it in addNodeRequest
            addNodeRequest['name'] = nodeDetail['name']

        parentNode = addNodeRequest['parentNode']
        dbHardwareProfile = addNodeRequest['hardwareprofile']
        dbSoftwareProfile = addNodeRequest['softwareprofile'] \
            if 'softwareprofile' in addNodeRequest else None

        addNodeRequest['nics'] = \
            self.__generate_nics(nodeDetail, dbHardwareProfile)

        if not addNodeRequest['nics']:
            raise InvalidArgument('No nics configured for node')

        node = self.nodeApi.createNewNode(
            dbSession, addNodeRequest, dbHardwareProfile,
            dbSoftwareProfile, validateIp=False)

        # Create the VM on the hypervisor
        self.__createVm(addNodeRequest, node)

        # VMware does not have the concept of a VM without a parent.
        # We cannot create idle nodes that aren't associated with
        # a hypervisor
        if parentNode:
            node.parentnode = parentNode

        if node.isIdle or not dbSoftwareProfile:
            # Do not start nodes that are idle or do not have an associated
            # software profile.
            return

        # Start VM
        configDict = addNodeRequest['configDict']

        self.__startVm(node,
                       bThinProvision=configDict['thin_provision'],
                       hardwareprofile=dbHardwareProfile,
                       softwareprofile=dbSoftwareProfile)

        return node

    def __create_node_from_template(self, addNodeRequest, newNodes,
                                    dbSession):
        dbHardwareProfile = addNodeRequest['hardwareprofile']
        dbSoftwareProfile = addNodeRequest['softwareprofile'] \
            if 'softwareprofile' in addNodeRequest else None

        configDict = addNodeRequest['configDict']

        parentNode = addNodeRequest['parentNode']

        vm_template_name = configDict['template'] \
            if 'template' in configDict else None

        # Generate a bogus name for the VM
        vmname = str(uuid.uuid4())

        msg = 'Cloning VM [%s] from VM template [%s].' % (
            vmname, vm_template_name)

        self.__instance_cache_set_unassigned(vmname)

        self.getLogger().debug('[vmware] ' + msg)

        # Clone the VM
        self.__cloneVm(vm_template_name,
                       vmname,
                       hypervisorNode=parentNode)

        self.getLogger().debug('[vmware] VM clone [%s] complete' % (vmname))

        server = self.__connect_vsphere()

        vm = self.__get_vm_by_name(server, vmname)

        # Wait for VMware Tools to be available
        vm.wait_for_tools(timeout=120)

        vm_hostname = ip_address = None

        count = 0

        # TODO: make this retry count and timeout period a configurable
        while count < 10:
            # Get newly cloned virtual machine object
            vm = self.__get_vm_by_name(server, vmname)

            ip_address = vm.get_property('ip_address')

            vm_hostname = vm.get_property('hostname', from_cache=False)

            if ip_address and vm_hostname:
                break

            time.sleep(10)

            count += 1

        if count == 10:
            msg = 'Unable to determine IP address of VM [%s]' % (vmname)

            self.getLogger().error('[vmware] ' + msg)

            raise CommandFailed(
                'Unable to communicate with VM [%s].'
                ' Is VMware Tools installed?')

        msg = ('VM [%s] was assigned host name [%s],'
               ' IP address [%s]' % (vmname, vm_hostname, ip_address))

        self.getLogger().debug('[vmware] ' + msg)

        # If the returned host name is not fully-qualified, append the
        # public DNS domain name.
        if '.' not in vm_hostname and self._domainname:
            # TODO:this might need to be user-configurable in case the
            # VM DNS domain is differenet from the domain of the Tortuga
            # installer.
            hostname = '%s.%s' % (vm_hostname, self._domainname)
        else:
            # Use host name 'as is'
            hostname = vm_hostname

        addNodeRequest['name'] = hostname

        addNodeRequest['nics'] = [
            {
                'device': 0,
                'ip': ip_address,
            },
        ]

        node = self.nodeApi.createNewNode(
            dbSession, addNodeRequest, dbHardwareProfile,
            dbSoftwareProfile, validateIp=False, bGenerateIp=False)

        msg = 'Node [%s] (%s) added' % (hostname, ip_address)

        # Cache the UUID to hostname mapping locally
        self.__instance_cache_set(hostname, vmname)

        # Remove the vmname from the unassigned instance cache
        self.__instance_cache_clear_unassigned(vmname)

        # Bootstrap the VM via VMware Tools
        configDict = addNodeRequest['configDict']

        if 'guest_username' not in configDict or \
                'guest_username' is None or \
                'guest_password' not in configDict or \
                'guest_password' is None:

            # Check if 'guest_bootstrap_cmd' is defined and use it. This
            # assumes SSH key-based authentication is previously
            # established.

            if 'guest_bootstrap_cmd' in configDict and \
                    configDict['guest_bootstrap_cmd'] is not None:

                # Simple Puppet-based bootstrap command
                # cmd = ('/usr/bin/puppet agent --verbose --onetime'
                #        ' --waitforcert 60 --server %s' % (self.fqdn))

                cmd = configDict['guest_bootstrap_cmd'].replace('@SERVER@',
                                                                self.fqdn)

                # TODO: should we use the IP address here simply because
                # it is available to us?
                ssh_cmd = 'ssh root@%s %s' % (ip_address, cmd)

                tortugaSubprocess.executeCommandAndIgnoreFailure(ssh_cmd)
            else:
                # Nothing to do!

                msg = ('VMware Tools guest username/password not set.'
                       ' Bootstrap will be skipped.')

                self.getLogger().warning('[vmware] ' + msg)

                return node
        else:
            msg = 'Boostrapping VM [%s] via VMware Tools' % (vmname)

            self.getLogger().debug('[vmware] ' + msg)

            try:
                self.__bootstrap_vm(vm, vmname, configDict)
            except CommandFailed as ex:
                self.getLogger().error(
                    '[vmware] Unable to bootstrap VM [%s] reason=[%s]:' % (
                        vmname, str(ex)))

        return node

    def __bootstrap_vm(self, vm, vmname, configDict):
        """
        Raises:
            CommandFailed
        """

        try:
            vm.login_in_guest(configDict['guest_username'],
                              configDict['guest_password'])
        except VIException as ex:
            raise CommandFailed(str(ex))

        cmd = ('/usr/bin/puppet'
               ' agent'
               ' --verbose'
               ' --waitforcert 60'
               ' --server %s'
               ' --onetime'
               ' --no-daemonize' % (self.fqdn))

        cmds = cmd.split(' ')

        pid = vm.start_process(cmds[0], args=cmds[1:])

        self.getLogger().\
            debug('[vmware] Running Puppet bootstrap for VM [%s]:'
                  ' cmd=[%s]' % (vmname, cmd))

        self.getLogger().\
            debug('[vmware] Puppet bootstrap PID=[%s]' % (pid))

    def __instance_cache_load(self):
        cfg = configparser.ConfigParser()

        cfg.read(self.instance_cache_path)

        return cfg

    def __instance_cache_save(self, cfg):
        with open(self.instance_cache_path, 'w') as fp:
            cfg.write(fp)

    def __instance_cache_get(self, vm_hostname):
        cfg = self.__instance_cache_load()

        if not cfg.has_section('instances') or \
                not cfg.has_option('instances', vm_hostname):
            return None

        return cfg.get('instances', vm_hostname)

    def __instance_cache_set(self, name, vmname=None):
        """
        Update instance cache (used currently for cloned VMs only)
        """

        cfg = self.__instance_cache_load()

        if vmname is not None:
            if not cfg.has_section('instances'):
                cfg.add_section('instances')

            cfg.set('instances', name, vmname)
        else:
            if cfg.has_section('instances'):
                cfg.remove_option('instances', name)

        self.__instance_cache_save(cfg)

    def __instance_cache_set_unassigned(self, vmname):
        cfg = self.__instance_cache_load()

        if not cfg.has_section('unassigned'):
            cfg.add_section('unassigned')

        cfg.set('unassigned', vmname, 'unassigned')

        self.__instance_cache_save(cfg)

    def __instance_cache_clear_unassigned(self, vmname):
        cfg = self.__instance_cache_load()

        if not cfg.has_section('unassigned'):
            return

        cfg.remove_option('unassigned', vmname)

        self.__instance_cache_save(cfg)

    def stop(self, hardwareProfileName, deviceName): \
            # pylint: disable=unused-argument
        pass

    def __getDedicatedServer(self, softwareProfile,
                             remainingNodeList=None):
        '''
        Method for choosing the optimal hypervisor to place a VM on

        Raises:
            NoFreeResourcesAvailable if no hypervisors are available
        '''

        # This is a placeholder until the VMware web API is fully
        # integrated. For now, it just picks the hypervisor with least
        # number of existing virtual machines

        if not remainingNodeList:
            # Get list of all hypervisor candidates
            remainingNodeList = softwareProfile.nodes
        else:
            remainingNodeList = [
                dbNode for dbNode in softwareProfile.nodes
                if dbNode.name in remainingNodeList
            ]

        if not remainingNodeList:
            raise NoFreeResourcesAvailable(
                'No VMware hypervisors available')

        # If there's only one hypervisor, return it
        if len(remainingNodeList) == 1:
            return remainingNodeList[0]

        vmCount = {}

        for hypervisorNode in remainingNodeList:
            vms_on_hypervisor = self.__getVmList(hypervisorNode.name)

            vmCount[len(vms_on_hypervisor)] = hypervisorNode

        # Find minimum
        lowest = sorted(vmCount.keys())

        return vmCount[lowest[0]]

    def __getBridgeNameMap(self, dbNics, hypervisorNode):
        """
        Raises:
            CommandFailed
            MissingHypervisorNetwork
            TortugaException
        """

        cfg_filename = os.path.join(
            self._cm.getKitConfigBase(), 'adapter-defaults-vmware.conf')

        cfg = configparser.ConfigParser()

        cfg.read(cfg_filename)

        section_name = hypervisorNode.name \
            if hypervisorNode else 'networks'

        if not cfg.has_section(section_name):
            if hypervisorNode:
                raise MissingHypervisorNetwork(
                    'Missing Tortuga network configuration for'
                    ' hypervisor [%s]. Check config file [%s]' % (
                        hypervisorNode, cfg_filename))

            raise CommandFailed(
                'Missing Tortuga network configuration for VMware.'
                ' Check config file [%s]' % (cfg_filename))

        network_bridge_list = []

        # Sort list of dbNics by device name
        dbNics.sort(nic_comparator)

        # Find the hypervisor bridges
        for dbNic in dbNics:
            networkStr = '%s/%s' % (
                dbNic.network.address, dbNic.network.netmask)

            if not cfg.has_option(section_name, networkStr):
                raise NetworkNotFound(
                    'Unable to find network configuration for network'
                    ' [%s]' % (networkStr))

            values = cfg.get(section_name, networkStr)

            items = values.split(',')

            if len(items) != 2:
                raise TortugaException(
                    'Malformed network configuration [%s]' % (cfg_filename))

            network_bridge_list.append({
                'network': networkStr,
                'switchName': items[0],
                'networkName': items[1],
                'mac': dbNic.mac,
            })

        return network_bridge_list

    def __get_customization_spec(self, name='Compute Default'):
        conn = self.__connect_vsphere()

        request = VI.GetCustomizationSpecRequestMsg()

        mor_spec_manager = request.\
            new__this(conn._do_service_content.CustomizationSpecManager)

        mor_spec_manager.set_attribute_type(MORTypes.CustomizationSpecManager)

        request.set_element__this(mor_spec_manager)

        # request.set_element_name('Compute Default')
        request.set_element_name(name)

        return conn._proxy.GetCustomizationSpec(request)._returnval

    def __get_datastore_mor(self, conn, crprops, hostmor, datastore): \
            # pylint: disable=unused-argument
        """
        Raises:
            CommandFailed
        """

        request = VI.QueryConfigTargetRequestMsg()
        _this = request.new__this(crprops.environmentBrowser._obj)
        _this.\
            set_attribute_type(
                crprops.environmentBrowser._obj.get_attribute_type())
        request.set_element__this(_this)
        h = request.new_host(hostmor)
        h.set_attribute_type(hostmor.get_attribute_type())
        request.set_element_host(h)

        config_target = conn._proxy.QueryConfigTarget(request)._returnval

        ds = None
        for d in config_target.Datastore:
            if d.Datastore.Accessible and \
                    d.Datastore.Name == self.__dataStore:
                ds = d.Datastore.Datastore
                datastorename = d.Datastore.Name
                break

        if not ds:
            raise CommandFailed(
                'Datastore [%s] not found' % (self.__dataStore))

        return VIMor(datastorename, MORTypes.Datastore)

    def __cloneVm(self, template_name, name, hypervisorNode=None):
        """Create new virtual machine using specified template.

        Raises:
            VirtualMachineNotFound
        """

        # These are sane defaults for now. We might expose them later.
        sync_run = True
        power_on = True

        # Connect to vCenter
        conn = self.__connect_vsphere()

        template_vm = self.__get_vm_by_name(conn, template_name)

        hostname = hypervisorNode.name if hypervisorNode else None

        # Get the MOR for the defined datacenter
        dcmor = self.__get_datacenter_mor(conn, self.__vCenterDataCenter)

        # If cluster is defined, find cluster MOR within datacenter.
        c_mor = self.\
            __get_cluster_mor_by_name(conn, dcmor, self.__vCenterCluster) \
            if self.__vCenterCluster else None

        dcprops = VIProperty(conn, dcmor)

        hfmor = dcprops.hostFolder._obj

        # Get the MOR of the destination folder, otherwise use the MOR of
        # the datacenter itself.
        folder_mor = self.__get_folder_mor(
            conn, dcmor, self.__destinationPath) \
            if self.__destinationPath else dcprops.vmFolder._obj

        # Get ComputeResource MORs used to determine the host where this
        # VM will be created.
        crmors = conn._retrieve_properties_traversal(
            property_names=['name', 'host'], from_node=hfmor,
            obj_type='ComputeResource')

        # If the hypervisor host name is not specified, choose a random
        # host from the cluster, if defined, otherwise from the datacenter.
        if hostname is None:
            # If a cluster is defined, use only hosts within that cluster,
            # otherwise use any host in the datacenter.
            source_mor = c_mor if c_mor is not None else dcmor

            hostmor = self.__get_random_host_mor(conn, source_mor)
        else:
            # Find MOR for specified host name
            hostmor = self.__get_host_mor_by_hostname(conn, dcmor, hostname)

        crmor = self.\
            __get_compute_resource_mor_by_host_mor(crmors, hostmor)

        crprops = VIProperty(conn, crmor)

        # Get resource pool MOR from the configured resource pool,
        # otherwise from the default pool
        resourcepool_mor = self.\
            __get_resource_pool_mor_by_name(conn, self.__vCenterPool) \
            if self.__vCenterPool else crprops.resourcePool._obj

        try:
            request = VI.CloneVM_TaskRequestMsg()
            _this = request.new__this(template_vm._mor)
            _this.set_attribute_type(template_vm._mor.get_attribute_type())
            request.set_element__this(_this)
            request.set_element_folder(folder_mor)
            request.set_element_name(name)
            spec = request.new_spec()
            spec.set_element_powerOn(power_on)
            location = spec.new_location()

            spec.set_element_customization(
                self.__get_customization_spec().Spec)

            if resourcepool_mor:
                pool = location.new_pool(resourcepool_mor)
                pool.set_attribute_type(resourcepool_mor.get_attribute_type())
                location.set_element_pool(pool)

            if hostmor:
                hs = location.new_host(hostmor)
                hs.set_attribute_type(hostmor.get_attribute_type())
                location.set_element_host(hs)

            spec.set_element_location(location)
            spec.set_element_template(False)
            request.set_element_spec(spec)

            task = conn._proxy.CloneVM_Task(request)._returnval

            vi_task = VITask(task, conn)

            if sync_run:
                status = vi_task.wait_for_state([vi_task.STATE_SUCCESS,
                                                 vi_task.STATE_ERROR])
                if status == vi_task.STATE_ERROR:
                    raise VIException(vi_task.get_error_message(),
                                      FaultTypes.TASK_ERROR)

                return VIVirtualMachine(conn, vi_task.get_result()._obj)

            return vi_task
        except VI.ZSI.FaultException as e:
            raise VIApiException(e)


def nic_comparator(nic1, nic2):
    if nic1.networkdevice.name < nic2.networkdevice.name:
        return -1

    if nic1.networkdevice.name > nic2.networkdevice.name:
        return 1

    return 0
