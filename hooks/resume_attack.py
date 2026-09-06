#!/usr/bin/env python3
"""SessionStart adapter: report one read-only controller action for explicit focus."""

import json
import sys

from math_search import lifecycle


def main():
    result = lifecycle(json.load(sys.stdin))
    if result is not None:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
