## VMware vSphere® 5 Resource Adapter Kit

### Overview

The VMware vSphere® 5 Resource Adapter allows Tortuga to integrate with the
VMware vSphere® environment within Tortuga. Once configured, it is possible to
seamlessly create VMware vSphere® virtual machines in an existing VMware
vSphere® infrastructure.

Tortuga interacts only with a VMware vCenter Server. It is **not** capable of interacting directly with VMware vSphere® (ESXi) Hypervisors.

### Installing the VMware vSphere® Resource Adapter Kit

The VMware vSphere® Resource Adapter kit installs as a standard kit using
`install-kit`:

    install-kit kit-vmwareadapter-6.3.0-0.tar.bz2

Enable the management component to complete the installation:

    enable-component -p vmwareadapter-6.3.0-0 management-6.3
    /opt/puppetlabs/bin/puppet agent --onetime --no-daemonize

This registers the resource adapter in Tortuga, and provides the bindings in
Tortuga to allow it to integrate with a VMware vSphere® infrastructure.

### Configuration

The VMware vSphere® resource adapter *must* be configured before it is used.

There are two configuration files that must be properly configured in order for
Tortuga to function correctly:

- `$TORTUGA_ROOT/config/vmware_vcenter_auth.conf`
- `$TORTUGA_ROOT/config/adapter-defaults-vmware.conf`

Consult your site's VMware vSphere® administrator for specifics required in the
configuration of the VMware vSphere® resource adapter.

#### adapter-defaults-vmware.conf

The configuration file `adapter-defaults-vmware.conf` specifies the host name
of the VMware vSphere® vCenter Server, user credentials, and the necessary
settings specific to your site's VMware vSphere® infrastructure.

The configuration file section `[vcenter]` has the following options:

- **hostname** (*mandatory*) -- host name or IP address of the VMware vSphere® vCenter server
- **username** (*mandatory*) -- user name of a VMware vSphere® vCenter user with sufficient rights for creating/deleting, starting/stopping, and configuring virtual machines created by Tortuga.

    Although, the "Administrator" user has such rights, it is *recommended* to create a VMware vSphere® user for Tortuga.
- **password** (*mandatory*) -- password for the abovementioned user name
- **datacenter** (*mandatory*) -- Name of VMware vSphere® datacenter where Tortuga will create virtual machines
- **pool** (*optional*)    -- Name of the resource pool where Tortuga will create virtual machines
- **path** (*optional*)    -- Resource path where Tortuga will create virtual machines.

    The path must be defined relative to the datacenter. For example, if the full path (including the datacenter) is `/datacenter1/accounting/tortuga`, the datacenter should be configured as `datacenter1` and the path should be `accounting/tortuga`:

        ...
        datacenter = datacenter1
        path = accounting/tortuga
        ...

    If the `path` is not specified, virtual machines created by Tortuga will be directly under the datacenter folder.

The configuration file section `[datastore]` has the following options:

- **label** (*mandatory*) -- name of the datastore that will be used to contain virtual disks created by Tortuga

#### adapter-defaults-vmware.conf

The file `adapter-defaults-vmware.conf` contains the default properties of virtual machines created and provisioned by Tortuga. These are defined in the section `[resource-adapter]`:

- **ramsize** (default: 1024) -- Amount of memory (in MB) to allocate to Tortuga created virtual machines.
- **ncpus** (default: 1) -- Number of virtual CPUs to allocate to Tortuga created virtual machines.
- **disksize** (default: 8) -- Size (in GB) of virtual hard disk allocated to Tortuga created virtual machines.

These settings take effect immediately and only virtual machines created after
the settings have changed will be affected.

The network configuration is also contained within this configuration file. The
configuration of networking can happen in one of two ways- per hypervisor or
globally. The "per hypervisor" method is only relevent when explicitly defining
which VMware vSphere® hypervisors will be used by Tortuga. The default
behaviour in Tortuga is to automatically use any and all available hypervisors
within the specified datacenter (as defined in `vmware_vcenter_auth.conf`).

The `[networks]` section specifies the global mapping of VMware vSphere®
virtual network to Tortuga provisioning network.

For example, to map the network `172.16.0.0/255.255.255.0` in Tortuga to the
VMware vSphere® virtual network named "VM Network" connected to VMware virtual
switch **vSwitch0**, use the following configuration file syntax:

    ...
    [networks]
    172.16.0.0/255.255.255.0 = vSwitch0,VM Network
    ...

**Note: it is not uncommon to have multiple entries for provisioning and/or public networks.**

The VMware vSphere® network specified in this mapping must be directly
connected to the Tortuga installer. Without connectivity, it will not be
possible for Tortuga to provision nodes on this interface.

\newpage

### VMware vSphere User Permissions

As previously mentioned, it is *strongly recommended* that the resource adapter
be configured to use a VMware vCenter user other than Administrator.

This non-Administrator VMware vCenter user must have the following privileges:

- Datastore
    - Allocate space
    - Browse datastore
- Host
    - Local Operations
        - Create virtual machine
        - Delete virtual machine
        - Manage user groups
        - Reconfigure virtual machine
- Network
    - Assign network
- Resource
    - Assign virtual machine to resource pool
- Virtual Machine
    - (*all privileges*)

Hint: use the "Roles" functionality in vSphere to assign the privileges to
multiple Windows users.

Consult your site's VMware vSphere® administrator for additional information
regarding user creation and/or assigning privileges to Windows users and/or
VMware vSphere users.

\newpage

### Creating VMware vSphere® Virtual Machines

VMware vSphere® virtual machines are created using standard Tortuga techniques:

1. Create a hardware profile representing VMware vSphere® compute nodes

        create-hardware-profile --name vmware

    Modify the hardware profile to use the VMware vSphere® resource adapter:

        update-hardware-profile --name vmware --resource-adapter vmware_adapter

    This step is essential to ensure Tortuga is configured to use VMware vSphere® to create nodes associated with this hardware profile.

    Perform any other hardware profile customization (ie. host name format) at this stage.

1. (*optional*) Create software profile for VMware vSphere® compute nodes

    This step is unnecessary if another compute software profile has been created elsewhere as part of the Tortuga setup process.

        create-software-profile --name Compute

    In order to successfully create a software profile for compute nodes, it is necessary for OS media to be installed and provisioning network must be setup correctly in Tortuga.

1. (*optional*) Enable Tortuga provisioning components

    If not already enabled, it is also necessary to enable the provisioning components:

        enable-component -p base-6.3.0-0 dhcpd-6.3
        enable-component -p base-6.3.0-0 dns-6.3
        /opt/puppetlabs/bin/puppet agent --onetime --no-daemonize

    **Hint:** Change DNS configuration (in `$TORTUGA_ROOT/config/base/dns-component.comf`) to use [Dnsmasq][] prior to running the `/opt/puppetlabs/bin/puppet agent` command, if desired.

1. Add one VMware vSphere® compute node with the following command

        add-nodes -n1 --software-profile Compute --hardware-profile vmware

    If everything is properly configured, the newly added compute node will be created and immediately begin provisioning. Any misconfiguration in the VMware vSphere® configuration files will result in failure and an error message output to the console.

    Tortuga will randomly select the hypervisor used by the compute node based on those available within the configured datacenter.

\newpage

### Advanced Topics

#### Manually Specifying Hypervisors

In a VMware vSphere® environment where not all hypervisors are available to or
should be used by Tortuga, it is possible to manually configure the available
hypervisors.

1. Create a hardware profile where VMware vSphere® hypervisors will represented

        create-hardware-profile --name Hypervisors --no-defaults
        update-hardware-profile --name Hypervisors --name-format '*' \
            --location remote

1. Create a software profile where VMware vSphere® hypervisors will be represented

        create-software-profile --name Hypervisors

1. Map the software and hardware profiles

        set-profile-mapping --software-profile Hypervisors \
            --hardware-profile Hypervisors

1. Add one or more VMware vSphere® hypervisors. The host names of these hypervisors can be determined by browsing the datacenter in the VMware vSphere® Web Client.

        add-nodes --host-name hypervisor01.local --software-profile Hypervisors \
            --hardware-profile Hypervisors

    Adjust the host name "`hypervisor01.local`" to properly reflect those chosen from your VMware vSphere® datacenter. Add as many hypervisors here as needed.

    Manually set node status to "Installed". This step is necessary to inform Tortuga of the availability of the specified hypervisors. Setting the node status to something other "Installed" effectively marks the hypervisor(s) as being *offline*.

        update-node-status --node hypervisor01.local --status Installed

1. Following the procedure described in the section "Creating VMware vSphere®
   Virtual Machines" to create hardware and software profiles for VMware
   vSphere®-based compute nodes.

    Ensure the hardware profile created in this step is properly configured to use the hypervisor(s) added above.

    Configure the compute node hardware profile to instruct Tortuga to use hypervisors defined in the **Hypervisors** software profile:

        update-hardware-profile --name vmware --hypervisor-profile Hypervisors

\newpage

### VMware vSphere® Troubleshooting

#### Unregistered/remnant virtual machines

If Tortuga attempts to add a virtual machine with the same name as an existing,
but unregistered virtual machine, the resource adapter will display an error
similar to the following:

        Could not find a VM with path '[datastore1] vm-01.private/vm-01.private.vmx'

Because of a built-in automatic renaming mechanism in VMware vCenter Server, it
will automatically create the new VM in a different folder than expected. This
results in the resource adapter being unable to find the files associated with
that VM.

The suggested workaround is to either move or rename the previously existing VM
folder within the datastore. If this is not possible, it is necessary to change
the host name format of Tortuga-created VMware virtual machines.
