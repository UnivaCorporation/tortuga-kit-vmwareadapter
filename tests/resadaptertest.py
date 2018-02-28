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


res = tortuga.resourceAdapter.vmware_adapter.Vmware_adapter()

server = res._Vmware_adapter__connect_vsphere()

dcmor = res._Vmware_adapter__get_datacenter_mor(server, 'Univa')

dcprops = VIProperty(server, dcmor)

hfmor = dcprops.hostFolder._obj

crmors = server._retrieve_properties_traversal(
    property_names=['name', 'host'], from_node=hfmor,
    obj_type='ComputeResource')

# print server.get_hosts()

hostmor = random.choice(list(server.get_hosts().keys()))
# hostmor = 'mike-3'

# p = VIProperty(server, hostmor)

# print p.runtime.bootTime
# print p.summary.config.name
# print dir(p.summary.config)

print(dcmor)

print(server.get_hosts(from_mor=dcmor))
# print server.get_hosts(from_mor='datacenter-2')

sys.exit(0)

crmor = None
for cr in crmors:
    for p in cr.PropSet:
        print(dir(p))
        if p.Name != 'host':
            continue

        for h in p.Val.get_element_ManagedObjectReference():
            print(h)

            if h == hostmor:
                crmor = cr.Obj
                break
        else:
            continue

        break

print(crmor)
