#
# Copyright: (c) 2018, Ansible Project
# Copyright: (c) 2018, Abhijeet Kasurde <akasurde@redhat.com>
#
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = r'''
    name: vmware_vm_inventory
    plugin_type: inventory
    short_description: VMware Guest inventory source
    version_added: "2.7"
    author:
      - Abhijeet Kasurde (@Akasurde)
    description:
        - Get virtual machines as inventory hosts from VMware environment.
        - Uses any file which ends with vmware.yml, vmware.yaml, vmware_vm_inventory.yml, or vmware_vm_inventory.yaml as a YAML configuration file.
        - The inventory_hostname is always the 'Name' and UUID of the virtual machine. UUID is added as VMware allows virtual machines with the same name.
    extends_documentation_fragment:
      - inventory_cache
    requirements:
      - "Python >= 2.7"
      - "PyVmomi"
      - "requests >= 2.3"
      - "vSphere Automation SDK"
      - "vCloud Suite SDK"
    options:
        hostname:
            description: Name of vCenter or ESXi server.
            required: True
            env:
              - name: VMWARE_HOST
              - name: VMWARE_SERVER
        username:
            description: Name of vSphere user.
            required: True
            env:
              - name: VMWARE_USER
              - name: VMWARE_USERNAME
        password:
            description: Password of vSphere user.
            required: True
            env:
              - name: VMWARE_PASSWORD
        port:
            description: Port number used to connect to vCenter or ESXi Server.
            default: 443
            env:
              - name: VMWARE_PORT
        validate_certs:
            description:
            - Allows connection when SSL certificates are not valid.
            - Set to C(false) when certificates are not trusted.
            default: True
            type: bool
            env:
              - name: VMWARE_VALIDATE_CERTS
        with_tags:
            description:
            - Include tags and associated virtual machines.
            - Requires 'vSphere Automation SDK' library to be installed on the given controller machine.
            - Please refer following URLs for installation steps
            - U(https://code.vmware.com/web/sdk/65/vsphere-automation-python)
            default: False
            type: bool
        properties:
            description:
            - Specify the list of VMware schema properties associated with the VM.
            - These properties will be populated in hostvars of the given VM.
            - Each value in the list specifies the path to a specific property in VM object.
            type: list
            default: [ 'name', 'config.cpuHotAddEnabled', 'config.cpuHotRemoveEnabled',
                       'config.instanceUuid', 'config.hardware.numCPU', 'config.template',
                       'config.name', 'guest.hostName', 'guest.ipAddress',
                       'guest.guestId', 'guest.guestState', 'runtime.maxMemoryUsage',
                       'customValue'
                       ]
            version_added: "2.9"
        datacenters:
            description:
            - A list of names of the datacenters.
            version_added: "2.10"
            required: False
            type: list
        clusters:
            description:
            - A list of names of the clusters.
            version_added: "2.10"
            required: False
            type: list
        folders:
            description:
            - A list of folder name where VM reside.
            - Folder that contains the virtual machine for the virtual machine to match the filter.
            - This is not absolute or relative path to virtual machine but a single folder name.
            version_added: "2.10"
            required: False
            type: list
        esxi_hostsystems:
            description:
            - A list of ESXi host systems.
            version_added: "2.10"
            required: False
            type: list
        resource_pools:
            description:
            - A list of resource pools.
            version_added: "2.10"
            required: False
            type: list
        no_object_failure:
            description:
            - This parameter governs the behaviour when C(datacenters), C(hosts), C(folders)
              and C(resource_pools) are specified as filters to gather VMs information.
            - When set to C(silent), no warnings or failures will occur if filters specified but not found in the infrastructure.
            - When set to C(warn), warnings will occur if filters specified but not found in the infrastructure.
            - When set to C(error), failures will occur if filters specified and not found in the infrastructure.
            version_added: "2.10"
            required: False
            default: silent
            type: str
            choices:
            - silent
            - warn
            - error
'''

EXAMPLES = r'''
# Sample configuration file for VMware Guest dynamic inventory
    plugin: vmware_vm_inventory
    strict: False
    hostname: 10.65.223.31
    username: administrator@vsphere.local
    password: Esxi@123$%
    validate_certs: False
    with_tags: True

# Gather minimum set of properties for VMware guest
    plugin: vmware_vm_inventory
    strict: False
    hostname: 10.65.223.31
    username: administrator@vsphere.local
    password: Esxi@123$%
    validate_certs: False
    with_tags: False
    properties:
    - 'name'
    - 'guest.ipAddress'

# Use Datacenter, Cluster and Folder value to list VMs
    plugin: vmware_vm_inventory
    strict: False
    hostname: 10.65.200.241
    username: administrator@vsphere.local
    password: Esxi@123$%
    validate_certs: False
    with_tags: True
    datacenters:
    - Asia-Datacenter1
    clusters:
    - Asia-Cluster1
    folders:
    - dev
    - prod
'''

import ssl
import atexit
import json
from ansible.errors import AnsibleError, AnsibleParserError
from ansible.utils.display import Display

display = Display()

try:
    # requests is required for exception handling of the ConnectionError
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from pyVim import connect
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

try:
    from com.vmware.vapi.std_client import DynamicID
    from vmware.vapi.vsphere.client import create_vsphere_client
    from com.vmware.vcenter_client import Cluster, Datacenter, Folder, Host, ResourcePool, VM
    HAS_VSPHERE = True
except ImportError:
    HAS_VSPHERE = False


from ansible.plugins.inventory import BaseInventoryPlugin, Cacheable


class BaseVMwareInventory:
    def __init__(self, hostname, username, password, port, validate_certs, with_tags):
        self.hostname = hostname
        self.username = username
        self.password = password
        self.port = port
        self.with_tags = with_tags
        self.validate_certs = validate_certs
        self.content = None
        self.rest_content = None

    def do_login(self):
        """
        Check requirements and do login
        """
        self.check_requirements()
        self.si, self.content = self._login()
        self.rest_content = self._login_vapi()

    def _login_vapi(self):
        """
        Login to vCenter API using REST call
        Returns: connection object

        """
        session = requests.Session()
        session.verify = self.validate_certs
        if not self.validate_certs:
            # Disable warning shown at stdout
            requests.packages.urllib3.disable_warnings()

        server = self.hostname
        if self.port:
            server += ":" + str(self.port)
        client = create_vsphere_client(server=server,
                                       username=self.username,
                                       password=self.password,
                                       session=session)
        if client is None:
            raise AnsibleError("Failed to login to %s using %s" % (server, self.username))
        return client

    def _login(self):
        """
        Login to vCenter or ESXi server
        Returns: connection object

        """
        if self.validate_certs and not hasattr(ssl, 'SSLContext'):
            raise AnsibleError('pyVim does not support changing verification mode with python < 2.7.9. Either update '
                               'python or set validate_certs to false in configuration YAML file.')

        ssl_context = None
        if not self.validate_certs and hasattr(ssl, 'SSLContext'):
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
            ssl_context.verify_mode = ssl.CERT_NONE

        service_instance = None
        try:
            service_instance = connect.SmartConnect(host=self.hostname, user=self.username,
                                                    pwd=self.password, sslContext=ssl_context,
                                                    port=self.port)
        except vim.fault.InvalidLogin as e:
            raise AnsibleParserError("Unable to log on to vCenter or ESXi API at %s:%s as %s: %s" % (self.hostname, self.port, self.username, e.msg))
        except vim.fault.NoPermission as e:
            raise AnsibleParserError("User %s does not have required permission"
                                     " to log on to vCenter or ESXi API at %s:%s : %s" % (self.username, self.hostname, self.port, e.msg))
        except (requests.ConnectionError, ssl.SSLError) as e:
            raise AnsibleParserError("Unable to connect to vCenter or ESXi API at %s on TCP/%s: %s" % (self.hostname, self.port, e))
        except vmodl.fault.InvalidRequest as e:
            # Request is malformed
            raise AnsibleParserError("Failed to get a response from server %s:%s as "
                                     "request is malformed: %s" % (self.hostname, self.port, e.msg))
        except Exception as e:
            raise AnsibleParserError("Unknown error while connecting to vCenter or ESXi API at %s:%s : %s" % (self.hostname, self.port, e))

        if service_instance is None:
            raise AnsibleParserError("Unknown error while connecting to vCenter or ESXi API at %s:%s" % (self.hostname, self.port))

        atexit.register(connect.Disconnect, service_instance)
        return service_instance, service_instance.RetrieveContent()

    def check_requirements(self):
        """ Check all requirements for this inventory are satisfied"""
        if not HAS_REQUESTS:
            raise AnsibleParserError('Please install "requests" Python module as this is required'
                                     ' for VMware Guest dynamic inventory plugin.')
        elif not HAS_PYVMOMI:
            raise AnsibleParserError('Please install "PyVmomi" Python module as this is required'
                                     ' for VMware Guest dynamic inventory plugin.')
        if HAS_REQUESTS:
            # Pyvmomi 5.5 and onwards requires requests 2.3
            # https://github.com/vmware/pyvmomi/blob/master/requirements.txt
            required_version = (2, 3)
            requests_version = requests.__version__.split(".")[:2]
            try:
                requests_major_minor = tuple(map(int, requests_version))
            except ValueError:
                raise AnsibleParserError("Failed to parse 'requests' library version.")

            if requests_major_minor < required_version:
                raise AnsibleParserError("'requests' library version should"
                                         " be >= %s, found: %s." % (".".join([str(w) for w in required_version]),
                                                                    requests.__version__))

        if not HAS_VSPHERE and self.with_tags:
            raise AnsibleError("Unable to find 'vSphere Automation SDK' Python library which is required."
                               " Please refer this URL for installation steps"
                               " - https://code.vmware.com/web/sdk/65/vsphere-automation-python")

        if not all([self.hostname, self.username, self.password]):
            raise AnsibleError("Missing one of the following : hostname, username, password. Please read "
                               "the documentation for more information.")

    @staticmethod
    def _process_object_types(vobj, level=0):
        """For an object that is not a valid JSON type, loop over its properties
        and return them in a dictionary"""
        try:
            json.dumps(vobj)
            return vobj
        except Exception:
            rdata = {}
            properties = dir(vobj)
            properties = [str(x) for x in properties if not x.startswith('_')]
            properties = [x for x in properties if x not in ('Array', 'disabledMethod', 'declaredAlarmState')]
            properties = sorted(properties)

            for prop in properties:
                # Attempt to get the property, skip on fail
                try:
                    propToSerialize = getattr(vobj, prop)
                except Exception:
                    continue

                if callable(propToSerialize):
                    continue

                prop = prop.lower()
                if level + 1 <= 2:
                    try:
                        rdata[prop] = BaseVMwareInventory._process_object_types(
                            propToSerialize,
                            level=(level + 1)
                        )
                    except vim.fault.NoPermission:
                        pass

        return rdata

    @staticmethod
    def _get_object_prop(vm, attributes):
        """Safely get a property or return None"""
        result = vm
        for attribute in attributes:
            try:
                result = getattr(result, attribute)
            except (AttributeError, IndexError):
                return None
            # assure that result is valid JSON data
            result = BaseVMwareInventory._process_object_types(result)
        return result


class InventoryModule(BaseInventoryPlugin, Cacheable):

    NAME = 'community.vmware.vmware_vm_inventory'

    def verify_file(self, path):
        """
        Verify plugin configuration file and mark this plugin active
        Args:
            path: Path of configuration YAML file
        Returns: True if everything is correct, else False
        """
        valid = False
        if super(InventoryModule, self).verify_file(path):
            if path.endswith(('vmware.yaml', 'vmware.yml', 'vmware_vm_inventory.yaml', 'vmware_vm_inventory.yml')):
                valid = True

        return valid

    def parse(self, inventory, loader, path, cache=True):
        """
        Parses the inventory file
        """
        super(InventoryModule, self).parse(inventory, loader, path, cache=cache)

        cache_key = self.get_cache_key(path)

        config_data = self._read_config_data(path)

        # set _options from config data
        self._consume_options(config_data)

        self.pyv = BaseVMwareInventory(
            hostname=self.get_option('hostname'),
            username=self.get_option('username'),
            password=self.get_option('password'),
            port=self.get_option('port'),
            with_tags=self.get_option('with_tags'),
            validate_certs=self.get_option('validate_certs')
        )

        self.pyv.do_login()

        self.pyv.check_requirements()

        source_data = None
        if cache:
            cache = self.get_option('cache')

        update_cache = False
        if cache:
            try:
                source_data = self._cache[cache_key]
            except KeyError:
                update_cache = True

        using_current_cache = cache and not update_cache
        cacheable_results = self._populate_from_source(source_data, using_current_cache)

        if update_cache:
            self._cache[cache_key] = cacheable_results

    def _populate_from_cache(self, source_data):
        """ Populate cache using source data """
        hostvars = source_data.pop('_meta', {}).get('hostvars', {})
        for group in source_data:
            if group == 'all':
                continue
            else:
                self.inventory.add_group(group)
                hosts = source_data[group].get('hosts', [])
                for host in hosts:
                    self._populate_host_vars([host], hostvars.get(host, {}), group)
                self.inventory.add_child('all', group)

    def _handle_error(self, message):
        no_object_failure = self.get_option('no_object_failure')
        if no_object_failure == 'warn':
            display.warning(message)
        elif no_object_failure == 'error':
            raise AnsibleError(message)

    def _get_vm_filter_spec(self):
        vm_filter_spec = VM.FilterSpec()
        datacenters = self.get_option('datacenters')
        if datacenters:
            temp_dcs = []
            for datacenter_name in datacenters:
                dc_filter_spec = Datacenter.FilterSpec(names=set([datacenter_name]))
                datacenter_summaries = self.pyv.rest_content.vcenter.Datacenter.list(dc_filter_spec)
                if len(datacenter_summaries) > 0:
                    temp_dcs.append(datacenter_summaries[0].datacenter)
                else:
                    self._handle_error(message="Unable to find datacenter %s" % datacenter_name)
            vm_filter_spec.datacenters = set(temp_dcs)

        clusters = self.get_option('clusters')
        if clusters:
            temp_clusters = []
            for cluster_name in clusters:
                ccr_filter_spec = Cluster.FilterSpec(names=set([cluster_name]))
                cluster_summaries = self.pyv.rest_content.vcenter.Cluster.list(ccr_filter_spec)
                if len(cluster_summaries) > 0:
                    temp_clusters.append(cluster_summaries[0].cluster)
                else:
                    self._handle_error(message="Unable to find cluster %s" % cluster_name)
            vm_filter_spec.clusters = set(temp_clusters)

        folders = self.get_option('folders')
        if folders:
            temp_folders = []
            for folder_name in folders:
                folder_filter_spec = Folder.FilterSpec(names=set([folder_name]))
                folder_summaries = self.pyv.rest_content.vcenter.Folder.list(folder_filter_spec)
                if len(folder_summaries) > 0:
                    temp_folders.append(folder_summaries[0].folder)
                else:
                    self._handle_error(message="Unable to find folder %s" % folder_name)
            vm_filter_spec.folders = set(temp_folders)

        esxi_hosts = self.get_option('esxi_hostsystems')
        if esxi_hosts:
            temp_hosts = []
            for esxi_name in esxi_hosts:
                esxi_filter_spec = Host.FilterSpec(names=set([esxi_name]))
                esxi_summaries = self.pyv.rest_content.vcenter.Host.list(esxi_filter_spec)
                if len(esxi_summaries) > 0:
                    temp_hosts.append(esxi_summaries[0].host)
                else:
                    self._handle_error(message="Unable to find esxi hostsystem %s" % esxi_name)
            vm_filter_spec.folders = set(temp_hosts)

        resource_pools = self.get_option('resource_pools')
        if resource_pools:
            temp_rps = []
            for rp_name in resource_pools:
                rp_filter_spec = ResourcePool.FilterSpec(names=set([rp_name]))
                rp_summaries = self.pyv.rest_content.vcenter.ResourcePool.list(rp_filter_spec)
                if len(rp_summaries) > 0:
                    temp_rps.append(rp_summaries[0].resourcepool)
                else:
                    self._handle_error(message="Unable to find resource pool %s" % rp_name)
            vm_filter_spec.folders = set(temp_rps)

        return vm_filter_spec

    def _populate_from_source(self, source_data, using_current_cache):
        """
        Populate inventory data from direct source

        """
        if using_current_cache:
            self._populate_from_cache(source_data)
            return source_data

        cacheable_results = {'_meta': {'hostvars': {}}}
        hostvars = {}

        vm_filter_spec = self._get_vm_filter_spec()
        objects = self.pyv.rest_content.vcenter.VM.list(vm_filter_spec)
        if self.pyv.with_tags:
            tag_svc = self.pyv.rest_content.tagging.Tag
            tag_association = self.pyv.rest_content.tagging.TagAssociation

            tags_info = dict()
            tags = tag_svc.list()
            for tag in tags:
                tag_obj = tag_svc.get(tag)
                tags_info[tag_obj.id] = tag_obj.name
                if tag_obj.name not in cacheable_results:
                    cacheable_results[tag_obj.name] = {'hosts': []}
                    self.inventory.add_group(tag_obj.name)

        for o in objects:
            vm_obj = vim.VirtualMachine(o.vm, self.pyv.si._stub)

            if not vm_obj.config:
                # Sometime orphaned VMs return no configurations
                continue

            current_host = vm_obj.name + "_" + vm_obj.config.uuid

            if current_host not in hostvars:
                hostvars[current_host] = {}
                self.inventory.add_host(current_host)

                host_ip = vm_obj.guest.ipAddress
                if host_ip:
                    self.inventory.set_variable(current_host, 'ansible_host', host_ip)

                self._populate_host_properties(vm_obj, current_host)

                # Only gather facts related to tag if vCloud and vSphere is installed.
                if self.pyv.with_tags:
                    # Add virtual machine to appropriate tag group
                    vm_mo_id = vm_obj._GetMoId()
                    vm_dynamic_id = DynamicID(type='VirtualMachine', id=vm_mo_id)
                    attached_tags = tag_association.list_attached_tags(vm_dynamic_id)

                    for tag_id in attached_tags:
                        self.inventory.add_child(tags_info[tag_id], current_host)
                        cacheable_results[tags_info[tag_id]]['hosts'].append(current_host)

                # Based on power state of virtual machine
                vm_power = str(vm_obj.summary.runtime.powerState)
                if vm_power not in cacheable_results:
                    cacheable_results[vm_power] = {'hosts': []}
                    self.inventory.add_group(vm_power)
                cacheable_results[vm_power]['hosts'].append(current_host)
                self.inventory.add_child(vm_power, current_host)

                # Based on guest id
                vm_guest_id = vm_obj.config.guestId
                if vm_guest_id and vm_guest_id not in cacheable_results:
                    cacheable_results[vm_guest_id] = {'hosts': []}
                    self.inventory.add_group(vm_guest_id)
                cacheable_results[vm_guest_id]['hosts'].append(current_host)
                self.inventory.add_child(vm_guest_id, current_host)

        for host in hostvars:
            h = self.inventory.get_host(host)
            cacheable_results['_meta']['hostvars'][h.name] = h.vars

        return cacheable_results

    def _populate_host_properties(self, vm_obj, current_host):
        # Load VM properties in host_vars
        vm_properties = self.get_option('properties') or []

        field_mgr = self.pyv.content.customFieldsManager.field

        for vm_prop in vm_properties:
            if vm_prop == 'customValue':
                for cust_value in vm_obj.customValue:
                    self.inventory.set_variable(current_host,
                                                [y.name for y in field_mgr if y.key == cust_value.key][0],
                                                cust_value.value)
            else:
                vm_value = self.pyv._get_object_prop(vm_obj, vm_prop.split("."))
                self.inventory.set_variable(current_host, vm_prop, vm_value)
