# Copyright 2015 PerfKitBenchmarker Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Runs YCSB against Aerospike.

This benchmark runs two workloads against Aerospike using YCSB (the Yahoo! Cloud
Serving Benchmark).
Aerospike is described in perfkitbenchmarker.linux_packages.aerospike_server
YCSB and workloads described in perfkitbenchmarker.linux_packages.ycsb.
"""

import functools
from absl import flags
from perfkitbenchmarker import configs
from perfkitbenchmarker import disk
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.linux_packages import aerospike_server
from perfkitbenchmarker.linux_packages import ycsb

FLAGS = flags.FLAGS

# TODO(user): unify overrides into --client_machine_type/server_machine_type
flags.DEFINE_string('aerospike_client_machine_type', None,
                    'Machine type to use for the aerospike client if different '
                    'from aerospike server machine type.')
flags.DEFINE_string('aerospike_server_machine_type', None,
                    'Machine type to use for the aerospike server if different '
                    'from aerospike client machine type.')

BENCHMARK_NAME = 'aerospike_ycsb'
BENCHMARK_CONFIG = """
aerospike_ycsb:
  description: >
    Run YCSB against an Aerospike
    installation. Specify the number of YCSB VMs with
    --ycsb_client_vms.
  vm_groups:
    workers:
      vm_spec: *default_single_core
      disk_spec: *default_500_gb
      vm_count: null
      disk_count: 0
    clients:
      vm_spec: *default_dual_core
"""


def GetConfig(user_config):
  config = configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)

  if FLAGS.aerospike_storage_type == aerospike_server.DISK:
    if FLAGS.data_disk_type == disk.LOCAL:
      # Didn't know max number of local disks, decide later.
      config['vm_groups']['workers']['disk_count'] = (
          config['vm_groups']['workers']['disk_count'] or None)
    else:
      config['vm_groups']['workers']['disk_count'] = (
          config['vm_groups']['workers']['disk_count'] or 1)
  if FLAGS.aerospike_server_machine_type:
    vm_spec = config['vm_groups']['workers']['vm_spec']
    for cloud in vm_spec:
      vm_spec[cloud]['machine_type'] = FLAGS.aerospike_server_machine_type
  if FLAGS.aerospike_client_machine_type:
    vm_spec = config['vm_groups']['clients']['vm_spec']
    for cloud in vm_spec:
      vm_spec[cloud]['machine_type'] = FLAGS.aerospike_client_machine_type

  if FLAGS['aerospike_vms'].present:
    config['vm_groups']['workers']['vm_count'] = FLAGS.aerospike_vms

  if FLAGS['ycsb_client_vms'].present:
    config['vm_groups']['clients']['vm_count'] = FLAGS.ycsb_client_vms

  return config


def CheckPrerequisites(benchmark_config):
  """Verifies that the required resources are present.

  Raises:
    perfkitbenchmarker.data.ResourceNotFound: On missing resource.
  """
  ycsb.CheckPrerequisites()


def Prepare(benchmark_spec):
  """Prepare the virtual machines to run YCSB against Aerospike.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """
  loaders = benchmark_spec.vm_groups['clients']
  assert loaders, benchmark_spec.vm_groups

  # Aerospike cluster
  aerospike_vms = benchmark_spec.vm_groups['workers']
  assert aerospike_vms, 'No aerospike VMs: {0}'.format(
      benchmark_spec.vm_groups)

  seed_ips = [vm.internal_ip for vm in aerospike_vms]
  aerospike_install_fns = [functools.partial(aerospike_server.ConfigureAndStart,
                                             vm, seed_node_ips=seed_ips)
                           for vm in aerospike_vms]
  ycsb_install_fns = [functools.partial(vm.Install, 'ycsb')
                      for vm in loaders]

  vm_util.RunThreaded(lambda f: f(), aerospike_install_fns + ycsb_install_fns)
  benchmark_spec.executor = ycsb.YCSBExecutor(
      'aerospike',
      **{'as.host': aerospike_vms[0].internal_ip,
         'as.namespace': 'test'})


def Run(benchmark_spec):
  """Spawn YCSB and gather the results.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.

  Returns:
    A list of sample.Sample instances.
  """
  loaders = benchmark_spec.vm_groups['clients']
  aerospike_vms = benchmark_spec.vm_groups['workers']

  metadata = {
      'ycsb_client_vms':
          FLAGS.ycsb_client_vms,
      'num_vms':
          len(aerospike_vms),
      'Storage Type':
          FLAGS.aerospike_storage_type,
      'memory_size':
          int(aerospike_vms[0].total_memory_kb * 0.8),
      'service_threads':
          FLAGS.aerospike_service_threads,
      'replication_factor':
          FLAGS.aerospike_replication_factor,
  }

  samples = list(benchmark_spec.executor.LoadAndRun(loaders))

  for sample in samples:
    sample.metadata.update(metadata)

  return samples


def Cleanup(benchmark_spec):
  """Cleanup.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """
  def StopAerospike(server):
    server.RemoteCommand('cd %s && nohup sudo make stop' %
                         aerospike_server.AEROSPIKE_DIR)
    server.RemoteCommand('sudo rm -rf aerospike*')

  aerospike_vms = benchmark_spec.vm_groups['workers']
  vm_util.RunThreaded(StopAerospike, aerospike_vms)
