"""This directory is setup with configurations to run the main functional test.

It exercises a full analysis pipeline on a smaller subset of data.
"""
import os
import subprocess
import unittest
import shutil
import contextlib
import collections
import functools

from nose import SkipTest
from nose.plugins.attrib import attr


@contextlib.contextmanager
def make_workdir():
    remove_old_dir = True
    #remove_old_dir = False
    dirname = os.path.join(os.path.dirname(__file__), "colorspace_test_output")
    if remove_old_dir:
        if os.path.exists(dirname):
            shutil.rmtree(dirname)
        os.makedirs(dirname)
    orig_dir = os.getcwd()
    try:
        os.chdir(dirname)
        yield
    finally:
        os.chdir(orig_dir)

def expected_failure(test):
    """Small decorator to mark tests as expected failure.
    Useful for tests that are work-in-progress.
    """
    @functools.wraps(test)
    def inner(*args, **kwargs):
        try:
            test(*args, **kwargs)
        except Exception:
            raise SkipTest
        else:
            raise AssertionError('Failure expected')
    return inner

class ColorSpaceTest(unittest.TestCase):
    """Setup a full automated analysis and run the pipeline.
    """
    def setUp(self):
        self.data_dir = os.path.join(os.path.dirname(__file__), "data", "colorspace")

    def _get_config_yaml(self):
        std = os.path.join(self.data_dir, "default_config.yaml")
        return std

    @attr(colorspace=True)
    def test_1_colorspace_alignment(self):
        """Test colorspace alignment with alignment tools specified in YAML file
        """
        with make_workdir():
            cl = ["bcbio_nextgen.py",
                  self._get_config_yaml(),
                  os.path.join(self.data_dir, "reads"),
                  os.path.join(self.data_dir, "run_info-colorspace.yaml")]
            subprocess.check_call(cl)