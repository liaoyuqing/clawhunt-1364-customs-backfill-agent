"""模块四启动自检包：python -m prepublish.check --config <yaml>。

不在 __init__ 里 import .check：那会让 python -m prepublish.check 重复导入本模块并触发 RuntimeWarning。
"""

__all__ = ["check"]
