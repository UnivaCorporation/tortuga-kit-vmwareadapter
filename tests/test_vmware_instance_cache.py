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

import pprint

from tortuga.resourceAdapter.vmware_adapter import Vmware_adapter


adapter = Vmware_adapter()

cfg = adapter._Vmware_adapter__instance_cache_load()

pprint.pprint(cfg.sections())

if 'instances' in cfg.sections():
    pprint.pprint(cfg.options('instances'))

adapter._Vmware_adapter__instance_cache_set('00', 'compute-0')
