#!/usr/bin/env python3
"""Stop adapter: the controller owns continuation, accounting and one summary."""

import json
import sys

from math_search import lifecycle


def main():
    result = lifecycle(json.load(sys.stdin), stop=True)
    if result is not None:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
