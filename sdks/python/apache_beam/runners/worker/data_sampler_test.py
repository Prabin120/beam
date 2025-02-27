#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# pytype: skip-file

import time
import unittest
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from apache_beam.coders import FastPrimitivesCoder
from apache_beam.coders import WindowedValueCoder
from apache_beam.portability.api import beam_fn_api_pb2
from apache_beam.runners.worker.data_sampler import DataSampler
from apache_beam.runners.worker.data_sampler import OutputSampler
from apache_beam.transforms.window import GlobalWindow
from apache_beam.utils.windowed_value import WindowedValue

MAIN_TRANSFORM_ID = 'transform'
MAIN_PCOLLECTION_ID = 'pcoll'
PRIMITIVES_CODER = FastPrimitivesCoder()


class FakeClock:
  def __init__(self):
    self.clock = 0

  def time(self):
    return self.clock


class DataSamplerTest(unittest.TestCase):
  def make_test_descriptor(
      self,
      outputs: Optional[List[str]] = None,
      transforms: Optional[List[str]] = None
  ) -> beam_fn_api_pb2.ProcessBundleDescriptor:
    outputs = outputs or [MAIN_PCOLLECTION_ID]
    transforms = transforms or [MAIN_TRANSFORM_ID]

    descriptor = beam_fn_api_pb2.ProcessBundleDescriptor()
    for transform_id in transforms:
      transform = descriptor.transforms[transform_id]
      for output in outputs:
        transform.outputs[output] = output

    return descriptor

  def setUp(self):
    self.data_sampler = DataSampler(sample_every_sec=0.1)

  def tearDown(self):
    self.data_sampler.stop()

  def wait_for_samples(
      self, data_sampler: DataSampler,
      pcollection_ids: List[str]) -> Dict[str, List[bytes]]:
    """Waits for samples to exist for the given PCollections."""
    now = time.time()
    end = now + 30

    samples = {}
    while now < end:
      time.sleep(0.1)
      now = time.time()
      samples.update(data_sampler.samples(pcollection_ids))

      if not samples:
        continue

      has_all = all(pcoll_id in samples for pcoll_id in pcollection_ids)
      if has_all:
        return samples

    self.assertLess(
        now,
        end,
        'Timed out waiting for samples for {}'.format(pcollection_ids))
    return {}

  def primitives_coder_factory(self, _):
    return PRIMITIVES_CODER

  def gen_sample(
      self,
      data_sampler: DataSampler,
      element: Any,
      output_index: int,
      transform_id: str = MAIN_TRANSFORM_ID):
    """Generates a sample for the given transform's output."""
    element_sampler = self.data_sampler.sampler_for_output(
        transform_id, output_index)
    element_sampler.el = element
    element_sampler.has_element = True

  def test_single_output(self):
    """Simple test for a single sample."""
    descriptor = self.make_test_descriptor()
    self.data_sampler.initialize_samplers(
        MAIN_TRANSFORM_ID, descriptor, self.primitives_coder_factory)

    self.gen_sample(self.data_sampler, 'a', output_index=0)

    expected_sample = {
        MAIN_PCOLLECTION_ID: [PRIMITIVES_CODER.encode_nested('a')]
    }
    samples = self.wait_for_samples(self.data_sampler, [MAIN_PCOLLECTION_ID])
    self.assertEqual(samples, expected_sample)

  def test_not_initialized(self):
    """Tests that transforms fail gracefully if not properly initialized."""
    with self.assertLogs() as cm:
      self.data_sampler.sampler_for_output(MAIN_TRANSFORM_ID, 0)
    self.assertRegex(cm.output[0], 'Out-of-bounds access.*')

  def map_outputs_to_indices(
      self, outputs, descriptor, transform_id=MAIN_TRANSFORM_ID):
    tag_list = list(descriptor.transforms[transform_id].outputs)
    return {output: tag_list.index(output) for output in outputs}

  def test_sampler_mapping(self):
    """Tests that the ElementSamplers are created for the correct output."""
    # Initialize the DataSampler with the following outputs. The order here may
    # get shuffled when inserting into the descriptor.
    pcollection_ids = ['o0', 'o1', 'o2']
    descriptor = self.make_test_descriptor(outputs=pcollection_ids)
    samplers = self.data_sampler.initialize_samplers(
        MAIN_TRANSFORM_ID, descriptor, self.primitives_coder_factory)

    # Create a map from the PCollection id to the index into the transform
    # output. This mirrors what happens when operators are created. The index of
    # an output is where in the PTransform.outputs it is located (when the map
    # is converted to a list).
    outputs = self.map_outputs_to_indices(pcollection_ids, descriptor)

    # Assert that the mapping is correct, i.e. that we can go from the
    # PCollection id -> output index and that this is the same as the created
    # samplers.
    index = outputs['o0']
    self.assertEqual(
        self.data_sampler.sampler_for_output(MAIN_TRANSFORM_ID, index),
        samplers[index])

    index = outputs['o1']
    self.assertEqual(
        self.data_sampler.sampler_for_output(MAIN_TRANSFORM_ID, index),
        samplers[index])

    index = outputs['o2']
    self.assertEqual(
        self.data_sampler.sampler_for_output(MAIN_TRANSFORM_ID, index),
        samplers[index])

  def test_multiple_outputs(self):
    """Tests that multiple PCollections have their own sampler."""
    pcollection_ids = ['o0', 'o1', 'o2']
    descriptor = self.make_test_descriptor(outputs=pcollection_ids)
    outputs = self.map_outputs_to_indices(pcollection_ids, descriptor)

    self.data_sampler.initialize_samplers(
        MAIN_TRANSFORM_ID, descriptor, self.primitives_coder_factory)

    self.gen_sample(self.data_sampler, 'a', output_index=outputs['o0'])
    self.gen_sample(self.data_sampler, 'b', output_index=outputs['o1'])
    self.gen_sample(self.data_sampler, 'c', output_index=outputs['o2'])

    samples = self.wait_for_samples(self.data_sampler, ['o0', 'o1', 'o2'])
    expected_samples = {
        'o0': [PRIMITIVES_CODER.encode_nested('a')],
        'o1': [PRIMITIVES_CODER.encode_nested('b')],
        'o2': [PRIMITIVES_CODER.encode_nested('c')],
    }
    self.assertEqual(samples, expected_samples)

  def test_multiple_transforms(self):
    """Test that multiple transforms with the same PCollections can be sampled.
    """
    # Initialize two transform both with the same two outputs.
    pcollection_ids = ['o0', 'o1']
    descriptor = self.make_test_descriptor(
        outputs=pcollection_ids, transforms=['t0', 't1'])
    t0_outputs = self.map_outputs_to_indices(
        pcollection_ids, descriptor, transform_id='t0')
    t1_outputs = self.map_outputs_to_indices(
        pcollection_ids, descriptor, transform_id='t1')

    self.data_sampler.initialize_samplers(
        't0', descriptor, self.primitives_coder_factory)

    self.data_sampler.initialize_samplers(
        't1', descriptor, self.primitives_coder_factory)

    # The OutputSampler is on a different thread so we don't test the same
    # PCollections to ensure that no data race occurs.
    self.gen_sample(
        self.data_sampler,
        'a',
        output_index=t0_outputs['o0'],
        transform_id='t0')
    self.gen_sample(
        self.data_sampler,
        'd',
        output_index=t1_outputs['o1'],
        transform_id='t1')
    expected_samples = {
        'o0': [PRIMITIVES_CODER.encode_nested('a')],
        'o1': [PRIMITIVES_CODER.encode_nested('d')],
    }
    samples = self.wait_for_samples(self.data_sampler, ['o0', 'o1'])
    self.assertEqual(samples, expected_samples)

    self.gen_sample(
        self.data_sampler,
        'b',
        output_index=t0_outputs['o1'],
        transform_id='t0')
    self.gen_sample(
        self.data_sampler,
        'c',
        output_index=t1_outputs['o0'],
        transform_id='t1')
    expected_samples = {
        'o0': [PRIMITIVES_CODER.encode_nested('c')],
        'o1': [PRIMITIVES_CODER.encode_nested('b')],
    }
    samples = self.wait_for_samples(self.data_sampler, ['o0', 'o1'])
    self.assertEqual(samples, expected_samples)

  def test_sample_filters_single_pcollection_ids(self):
    """Tests the samples can be filtered based on a single pcollection id."""
    pcollection_ids = ['o0', 'o1', 'o2']
    descriptor = self.make_test_descriptor(outputs=pcollection_ids)
    outputs = self.map_outputs_to_indices(pcollection_ids, descriptor)

    self.data_sampler.initialize_samplers(
        MAIN_TRANSFORM_ID, descriptor, self.primitives_coder_factory)

    self.gen_sample(self.data_sampler, 'a', output_index=outputs['o0'])
    self.gen_sample(self.data_sampler, 'b', output_index=outputs['o1'])
    self.gen_sample(self.data_sampler, 'c', output_index=outputs['o2'])

    samples = self.wait_for_samples(self.data_sampler, ['o0'])
    expected_samples = {
        'o0': [PRIMITIVES_CODER.encode_nested('a')],
    }
    self.assertEqual(samples, expected_samples)

    samples = self.wait_for_samples(self.data_sampler, ['o1'])
    expected_samples = {
        'o1': [PRIMITIVES_CODER.encode_nested('b')],
    }
    self.assertEqual(samples, expected_samples)

    samples = self.wait_for_samples(self.data_sampler, ['o2'])
    expected_samples = {
        'o2': [PRIMITIVES_CODER.encode_nested('c')],
    }
    self.assertEqual(samples, expected_samples)

  def test_sample_filters_multiple_pcollection_ids(self):
    """Tests the samples can be filtered based on a multiple pcollection ids."""
    pcollection_ids = ['o0', 'o1', 'o2']
    descriptor = self.make_test_descriptor(outputs=pcollection_ids)
    outputs = self.map_outputs_to_indices(pcollection_ids, descriptor)

    self.data_sampler.initialize_samplers(
        MAIN_TRANSFORM_ID, descriptor, self.primitives_coder_factory)

    self.gen_sample(self.data_sampler, 'a', output_index=outputs['o0'])
    self.gen_sample(self.data_sampler, 'b', output_index=outputs['o1'])
    self.gen_sample(self.data_sampler, 'c', output_index=outputs['o2'])

    samples = self.wait_for_samples(self.data_sampler, ['o0', 'o2'])
    expected_samples = {
        'o0': [PRIMITIVES_CODER.encode_nested('a')],
        'o2': [PRIMITIVES_CODER.encode_nested('c')],
    }
    self.assertEqual(samples, expected_samples)


class OutputSamplerTest(unittest.TestCase):
  def setUp(self):
    self.fake_clock = FakeClock()

  def tearDown(self):
    self.sampler.stop()

  def control_time(self, new_time):
    self.fake_clock.clock = new_time

  def wait_for_samples(self, output_sampler: OutputSampler, expected_num: int):
    """Waits for the expected number of samples for the given sampler."""
    now = time.time()
    end = now + 30

    while now < end:
      time.sleep(0.1)
      now = time.time()
      samples = output_sampler.flush(clear=False)

      if not samples:
        continue

      if len(samples) == expected_num:
        return samples

    self.assertLess(now, end, 'Timed out waiting for samples')

  def ensure_sample(
      self, output_sampler: OutputSampler, sample: Any, expected_num: int):
    """Generates a sample and waits for it to be available."""

    element_sampler = output_sampler.element_sampler

    now = time.time()
    end = now + 30

    while now < end:
      element_sampler.el = sample
      element_sampler.has_element = True
      time.sleep(0.1)
      now = time.time()
      samples = output_sampler.flush(clear=False)

      if not samples:
        continue

      if len(samples) == expected_num:
        return samples

    self.assertLess(
        now, end, 'Timed out waiting for sample "{sample}" to be generated.')

  def test_can_sample(self):
    """Tests that the underlying timer can sample."""
    self.sampler = OutputSampler(PRIMITIVES_CODER, sample_every_sec=0.05)
    element_sampler = self.sampler.element_sampler
    element_sampler.el = 'a'
    element_sampler.has_element = True

    samples = self.wait_for_samples(self.sampler, expected_num=1)
    self.assertEqual(samples, [PRIMITIVES_CODER.encode_nested('a')])

  def test_acts_like_circular_buffer(self):
    """Tests that the buffer overwrites old samples."""
    self.sampler = OutputSampler(
        PRIMITIVES_CODER, max_samples=2, sample_every_sec=0)
    element_sampler = self.sampler.element_sampler

    for i in range(10):
      element_sampler.el = i
      element_sampler.has_element = True
      self.sampler.sample()

    self.assertEqual(
        self.sampler.flush(),
        [PRIMITIVES_CODER.encode_nested(i) for i in (8, 9)])

  def test_samples_multiple_times(self):
    """Tests that the buffer overwrites old samples."""
    self.sampler = OutputSampler(
        PRIMITIVES_CODER, max_samples=10, sample_every_sec=0.05)

    # Always samples the first ten.
    for i in range(10):
      self.ensure_sample(self.sampler, i, i + 1)
    self.assertEqual(
        self.sampler.flush(),
        [PRIMITIVES_CODER.encode_nested(i) for i in range(10)])

  def test_can_sample_windowed_value(self):
    """Tests that values with WindowedValueCoders are sampled wholesale."""
    coder = WindowedValueCoder(FastPrimitivesCoder())
    value = WindowedValue('Hello, World!', 0, [GlobalWindow()])

    self.sampler = OutputSampler(coder, sample_every_sec=0)
    element_sampler = self.sampler.element_sampler
    element_sampler.el = value
    element_sampler.has_element = True
    self.sampler.sample()

    self.assertEqual(self.sampler.flush(), [coder.encode_nested(value)])

  def test_can_sample_non_windowed_value(self):
    """Tests that windowed values with WindowedValueCoders sample only the
    value.

    This is important because the Python SDK wraps all values in a WindowedValue
    even if the coder is not a WindowedValueCoder. In this case, the value must
    be retrieved from the WindowedValue to match the correct coder.
    """
    value = WindowedValue('Hello, World!', 0, [GlobalWindow()])

    self.sampler = OutputSampler(PRIMITIVES_CODER, sample_every_sec=0)
    element_sampler = self.sampler.element_sampler
    element_sampler.el = value
    element_sampler.has_element = True
    self.sampler.sample()

    self.assertEqual(
        self.sampler.flush(), [PRIMITIVES_CODER.encode_nested('Hello, World!')])


if __name__ == '__main__':
  unittest.main()
