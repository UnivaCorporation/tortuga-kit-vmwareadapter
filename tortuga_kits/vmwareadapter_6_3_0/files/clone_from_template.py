#!/usr/bin/env python

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

import sys
import random
import pysphere
from pysphere import VITask, MORTypes, VIProperty
import tortuga.resourceAdapter.vmware_adapter
from pysphere.vi_virtual_machine import VIVirtualMachine
from pysphere.resources import VimService_services as VI


res = tortuga.resourceAdapter.vmware_adapter.Vmware_adapter()

server = res._Vmware_adapter__connect_vsphere()

def _get_template_list(s):
    props = s._retrieve_properties_traversal(
        property_names=['name', 'config.template'], from_node=None,
        obj_type=MORTypes.VirtualMachine)

    result = []

    for p in props:
        mor = p.Obj
        name = ''
        is_template = False
        for item in p.PropSet:
            if item.Name == 'name':
                name = item.Val
            elif item.Name == 'config.template':
                is_template = item.Val
    
        if is_template:
            result.append((mor, name))
            # print 'MOR:', mor, ' - Name:', name

    return result

def __get_template_by_name(s, template_name):
    for mor, name in _get_template_list(server):
       if name == template_name:
           break
    else:
        raise Exception('Template [%s] not found' % (template_name))

    return mor

def __get_computeresource_mor(s, hfmor, host_mor):
    crmor = None

    for cr in s._retrieve_properties_traversal(
        property_names=['name', 'host'], from_node=hfmor,
        obj_type='ComputeResource'):
        # Iterate over ComputeResource host properties (only)
        for p in [_p for _p in cr.PropSet if _p.Name == 'host']:
            # Search for matching HostSystem MOR
            host_mors = [
                host_mor_ for host_mor_ in
                    p.Val.get_element_ManagedObjectReference()
                        if host_mor_ == host_mor
            ]

            if not host_mors:
                continue

            # Found ComputeResource with host
            crmor = cr.Obj

            break
        else:
            continue

    return crmor

# Retrieve datacenter MOR
dc_mor = res._Vmware_adapter__get_datacenter_mor(
    server, res._Vmware_adapter__vCenterDataCenter)

# Randomly select a target host
host_mor = random.choice(list(server.get_hosts(from_mor=dc_mor).keys()))

# Get hostFolder MOR
dc_properties = VIProperty(server, dc_mor)

hostFolder_mor = dc_properties.hostFolder._obj

# Get ComputeResource MOR for target host
cr_mor = __get_computeresource_mor(server, hostFolder_mor, host_mor)

# Get ComputeResource properties
crprops = VIProperty(server, cr_mor)

# Get ResourcePool MOR
rp_mor = crprops.resourcePool._obj

template_mor = __get_template_by_name(server, 'template2')

vm = VIVirtualMachine(server, template_mor)

folder = None

folders = server._retrieve_properties_traversal(
                                         property_names=['name', 'childEntity'],
                                         obj_type=MORTypes.Folder)

folder_mor = None
for f in folders:
    fname = ""
    children = []
    for prop in f.PropSet:
        if prop.Name == "name":
            fname = prop.Val
        elif prop.Name == "childEntity":
            children = prop.Val.ManagedObjectReference
    if folder == fname or (not folder and vm._mor in children):
        folder_mor = f.Obj
        break
if not folder_mor and folder:
    raise Exception("Couldn't find folder %s" % folder)
elif not folder_mor:
    raise Exception("Error locating current VM folder")


request = VI.CloneVM_TaskRequestMsg()

_this = request.new__this(vm._mor)
_this.set_attribute_type(vm._mor.get_attribute_type())

request.set_element__this(_this)
request.set_element_folder(folder_mor)
request.set_element_name('compute-99')

spec = request.new_spec()

# customization (CustomizationSpec)
customization = spec.new_customization()

# customization -> globalIPSettings
globalIPSettings = customization.new_globalIPSettings()
customization.set_element_globalIPSettings(globalIPSettings)

# customization -> identity (CustomizationLinuxPrep)
identity = VI.ns0.CustomizationLinuxPrep_Def('identity').pyclass()
identity.set_element_domain('isurfer.ca')
hostName = VI.ns0.CustomizationFixedName_Def('hostName').pyclass()
hostName.set_element_name('compute-99')
identity.set_element_hostName(hostName)
customization.set_element_identity(identity)

# customization -> nicSettingMap

adapter = VI.ns0.CustomizationIPSettings_Def('adapter').pyclass()

ip = VI.ns0.CustomizationDhcpIpGenerator_Def('ip').pyclass()

adapter.set_element_ip(ip)

nicSettingMap = customization.new_nicSettingMap()

nicSettingMap.set_element_adapter(adapter)

customization.set_element_nicSettingMap([nicSettingMap])

# Set customization
spec.set_element_customization(customization)

# location
location = spec.new_location()

if rp_mor:
    pool = location.new_pool(rp_mor)
    pool.set_attribute_type(rp_mor.get_attribute_type())
    location.set_element_pool(pool)

if host_mor:
    hs = location.new_host(host_mor)
    hs.set_attribute_type(host_mor.get_attribute_type())
    location.set_element_host(hs)

spec.set_element_location(location)

# powerOn
spec.set_element_powerOn(False)

# template
spec.set_element_template(False)

# Set the 'spec' element of the request
request.set_element_spec(spec)

# Execute the request
task = server._proxy.CloneVM_Task(request)._returnval

vi_task = VITask(task, server)

status = vi_task.wait_for_state([vi_task.STATE_SUCCESS, vi_task.STATE_ERROR])

if status == vi_task.STATE_ERROR:
    print('Failed')

    print(vi_task.get_error_message())
