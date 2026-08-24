# -*- coding: utf-8 -*-
import os
import sys

# 让 `import assistant...` 在未安装包时也能工作（src 布局）
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
