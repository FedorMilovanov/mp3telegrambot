#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ready-SRT child entrypoint with bounded independent-QA recovery."""
from tools.voxcpm2 import independent_qa_retry

from . import main


if __name__ == "__main__":
    independent_qa_retry.install()
    main()
