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

from tortuga.resourceAdapter.vmware_adapter import Vmware_adapter


def main():
    adapter = Vmware_adapter()

    server = adapter._Vmware_adapter__connect_vsphere()

    templates = []

    cc = server._retrieve_properties_traversal(
        property_names=['config.template', 'config.files.vmPathName'],
        obj_type='VirtualMachine')

    for o in cc:
        path, template = None, False

        if not hasattr(o, 'PropSet'):
            continue

        for p in o.PropSet:
            if p.Name == 'config.template' and p.Val:
                template = True

            if p.Name == 'config.files.vmPathName':
                path = p.Val

            if template:
                templates.append(path)

    print(templates)


if __name__ == '__main__':
    main()
