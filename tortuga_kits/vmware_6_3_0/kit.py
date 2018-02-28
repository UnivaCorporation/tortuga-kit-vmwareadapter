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

import glob
import os
import shutil

from tortuga.exceptions.hardwareProfileNotFound \
    import HardwareProfileNotFound
from tortuga.exceptions.softwareProfileNotFound \
    import SoftwareProfileNotFound
from tortuga.hardwareprofile.hardwareProfileFactory \
    import getHardwareProfileApi
from tortuga.kit.mixins import ResourceAdapterMixin
from tortuga.kit.installer import KitInstallerBase
from tortuga.kit.kitApiFactory import getKitApi
from tortuga.os_utility import tortugaSubprocess
from tortuga.softwareprofile.softwareProfileFactory \
    import getSoftwareProfileApi


DEFAULT_SOFTWARE_PROFILE_LIST = ['VMwareHypervisor']
DEFAULT_HARDWARE_PROFILE_LIST = ['VMware']


class VMwareInstaller(ResourceAdapterMixin, KitInstallerBase):
    puppet_modules = ['univa-tortuga_kit_vmware']
    resource_adapter_name = 'vmware'

    def __init__(self):
        super().__init__()
        self._kit_api = getKitApi()

    def _get_esx_os_component_list(self):
        os_list = []
        for kit in [kit for kit in self._kit_api.getKitList()
                    if kit.getIsOs()]:
            os_list.extend(kit.getComponentList())
        return os_list

    def action_post_install(self):
        super().action_post_install()

        #
        # Link VMwareTools into the files directory
        #
        vmware_tools_files = glob.glob(
            os.path.join(self.files_path, 'VMwareTools*tar.gz')
        )
        for link_src in vmware_tools_files:
            link_dst = os.path.join(
                '/etc/puppet/modules/tortuga_kit_vmware/files',
                os.path.basename(vmware_tools_files[0])
            )
            if not os.path.exists(os.path.dirname(link_dst)):
                os.makedirs(os.path.dirname(link_dst))
            _symlink(link_src, link_dst)

            #
            # Currently, install-vmware-tools (run on compute nods)
            # depends on the tarball being available in the private
            # webroot
            #
            tmp_link_dst = os.path.join(
                self.config_manager.getTortugaIntWebRoot(),
                os.path.basename(link_src)
            )
            _symlink(link_src, tmp_link_dst)

        #
        # Symlink config file to '<instroot>/config'
        #
        src_file = os.path.join(
            self.files_path,
            'adapter-defaults-vmware.conf'
        )
        dst_file = os.path.join(
            self.config_manager.getKitConfigBase(),
            os.path.basename(src_file)
        )
        shutil.copyfile(src_file, dst_file)

        src_file = os.path.join(self.files_path, 'vmware_vcenter_auth.conf')
        dst_file = os.path.join(
             self.config_manager.getKitConfigBase(),
             'vmware_vcenter_auth.conf'
        )
        shutil.copyfile(src_file, dst_file)

        #
        # Make scripts available for retrieval from the internal webserver
        #
        vmware_int_web_root = os.path.join(
            self.config_manager.getTortugaIntWebRoot(),
            'vmware'
        )
        if not os.path.exists(vmware_int_web_root):
            os.makedirs(vmware_int_web_root)

        #
        # Copy files to Puppet module files directory
        #
        src_file = os.path.join(self.files_path, 'vmware-tools-install')
        dst_file = os.path.join(
            '/etc/puppet/modules/tortuga_kit_vmware/files',
            os.path.basename(src_file)
        )
        if not os.path.exists(os.path.dirname(dst_file)):
            os.makedirs(os.path.dirname(dst_file))
        shutil.copyfile(src_file, dst_file)

    def action_post_uninstall(self, *args, **kwargs):
        super().action_post_install(*args, **kwargs)

        #
        # Remove symlink to modules
        #
        tortugaSubprocess.executeCommand(
            '/bin/rm -f {}/lib/tortuga/vmwareUtil'.format(self.getRoot()))

        #
        # Delete the hardware and software profiles we created in
        # post_install
        #
        hardware_profile_api = getHardwareProfileApi()
        software_profile_api = getSoftwareProfileApi()

        for hwp_name in DEFAULT_HARDWARE_PROFILE_LIST:
            try:
                hardware_profile_api.deleteHardwareProfile(hwp_name)
            except HardwareProfileNotFound:
                #
                # We can safely ignore this error
                #
                pass

        for swp_name in DEFAULT_SOFTWARE_PROFILE_LIST:
            try:
                software_profile_api.deleteSoftwareProfile(swp_name)
            except SoftwareProfileNotFound:
                #
                # We can safely ignore this error
                #
                pass


def _symlink(src, dst, b_force=True):
    if b_force and os.path.lexists(dst):
        os.unlink(dst)
    os.symlink(src, dst)
