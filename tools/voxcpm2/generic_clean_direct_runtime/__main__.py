#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ready-SRT child entrypoint with monolithic routing and bounded QA recovery."""
from tools.voxcpm2 import independent_qa_retry
from tools.voxcpm2 import monolithic_runtime_install

from . import main


if __name__ == "__main__":
    monolithic_runtime_install.install()
    independent_qa_retry.install()
    main()
