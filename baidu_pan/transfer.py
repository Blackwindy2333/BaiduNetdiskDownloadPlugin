"""分享文件精确转存：路径选择、自家分享直下与转存结果归一。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import TargetNotFoundError, TransferError
from .models import ShareNode, ShareTree


@dataclass
class TransferResult:
    """一次转存（或自家分享直下）的结果。"""

    own_share: bool
    target_dir: str
    filenames: list[str] = field(default_factory=list)
    direct_paths: list[str] = field(default_factory=list)
    nodes: list[ShareNode] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return self.filenames or self.direct_paths


def select_node(tree: ShareTree, path: str) -> ShareNode:
    """按目录树路径精确匹配；退化为唯一 basename 匹配。"""

    raw = str(path or "").strip()
    if not raw:
        raise TargetNotFoundError("请提供要操作的文件或目录路径。")
    node = tree.find(raw)
    if node is not None:
        return node
    matches = tree.find_by_basename(raw)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise TargetNotFoundError(f"「{raw}」存在多个同名项，请提供更完整的路径。")
    raise TargetNotFoundError()


def root_nodes(tree: ShareTree) -> list[ShareNode]:
    """返回分享根目录下的顶层节点。"""

    return [node for node in tree.nodes if node.path.strip("/").count("/") == 0]


def unique_nodes(nodes: list[ShareNode]) -> list[ShareNode]:
    seen: set[str] = set()
    result: list[ShareNode] = []
    for node in nodes:
        key = node.path or str(node.fs_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(node)
    return result


async def transfer_nodes(
    client: Any,
    tree: ShareTree,
    nodes: list[ShareNode],
    *,
    target_dir: str,
    account_uid: int | None = None,
) -> TransferResult:
    """把节点转存到 ``target_dir``；自己的分享改为直下原路径。

    Args:
        client: 具备 ``transfer(info, fs_ids, target_dir)`` 的 ShareClient。
        tree: 当前分享树。
        nodes: 选中的节点。
        target_dir: 网盘目标目录。
        account_uid: 当前登录账号 uid，用于判断自家分享。
    """

    selected = unique_nodes([node for node in nodes if node is not None])
    if not selected:
        raise TransferError("没有选中任何文件或目录。")

    if account_uid is not None and str(account_uid) == str(tree.info.uk):
        return TransferResult(
            own_share=True,
            target_dir=target_dir,
            direct_paths=[node.path for node in selected],
            nodes=selected,
        )

    fs_ids = [node.fs_id for node in selected if node.fs_id not in (None, "")]
    if not fs_ids:
        raise TransferError("选中项缺少 fs_id，无法转存。")

    response = await client.transfer(tree.info, fs_ids, target_dir)
    if response.get("own_share"):
        return TransferResult(
            own_share=True,
            target_dir=target_dir,
            direct_paths=[node.path for node in selected],
            nodes=selected,
        )

    filenames = [str(name) for name in (response.get("filenames") or []) if str(name).strip()]
    if not filenames:
        filenames = [node.name for node in selected]
    return TransferResult(own_share=False, target_dir=target_dir, filenames=filenames, nodes=selected)


__all__ = [
    "TransferResult",
    "root_nodes",
    "select_node",
    "transfer_nodes",
    "unique_nodes",
]
