"""转存服务：路径选择、自家分享直下与结果归一。"""

from __future__ import annotations

import pytest

from baidu_pan.errors import TargetNotFoundError, TransferError
from baidu_pan.models import ShareInfo, ShareNode, ShareTree
from baidu_pan.transfer import (
    root_nodes,
    select_node,
    transfer_nodes,
    unique_nodes,
)


def make_tree() -> ShareTree:
    nodes = [
        ShareNode(name="文件夹", path="/文件夹", is_dir=True, fs_id=1),
        ShareNode(name="a.txt", path="/文件夹/a.txt", is_dir=False, size=10, fs_id=2),
        ShareNode(name="b.txt", path="/b.txt", is_dir=False, size=20, fs_id=3),
        ShareNode(name="同名.txt", path="/x/同名.txt", is_dir=False, size=1, fs_id=4),
        ShareNode(name="同名.txt", path="/y/同名.txt", is_dir=False, size=1, fs_id=5),
    ]
    return ShareTree(info=ShareInfo(surl="1AbCdEf", share_id="1", uk="2"), nodes=nodes)


def test_select_node_exact() -> None:
    tree = make_tree()
    assert select_node(tree, "/b.txt").fs_id == 3
    assert select_node(tree, "b.txt").fs_id == 3


def test_select_node_basename_unique() -> None:
    tree = make_tree()
    assert select_node(tree, "a.txt").fs_id == 2


def test_select_node_ambiguous_and_missing() -> None:
    tree = make_tree()
    with pytest.raises(TargetNotFoundError):
        select_node(tree, "同名.txt")
    with pytest.raises(TargetNotFoundError):
        select_node(tree, "不存在.txt")
    with pytest.raises(TargetNotFoundError):
        select_node(tree, "")


def test_root_nodes_only_top_level() -> None:
    tree = make_tree()
    assert [node.name for node in root_nodes(tree)] == ["文件夹", "b.txt"]


def test_unique_nodes() -> None:
    tree = make_tree()
    nodes = [tree.nodes[0], tree.nodes[0], tree.nodes[1]]
    assert len(unique_nodes(nodes)) == 2


class FakeTransferClient:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self.response = response or {"own_share": False, "filenames": ["a.txt"], "errno": 0}
        self.error = error

    async def transfer(self, info, fs_ids, target_dir):
        self.calls.append((info.surl, list(fs_ids), target_dir))
        if self.error is not None:
            raise self.error
        return self.response


async def test_transfer_nodes_normal() -> None:
    tree = make_tree()
    client = FakeTransferClient()
    result = await transfer_nodes(
        client,
        tree,
        [tree.nodes[0], tree.nodes[1]],
        target_dir="/我的资源/临时",
        account_uid=999,
    )
    assert result.own_share is False
    assert result.filenames == ["a.txt"]
    assert client.calls == [("1AbCdEf", [1, 2], "/我的资源/临时")]


async def test_transfer_nodes_own_share_direct() -> None:
    tree = make_tree()
    client = FakeTransferClient()
    result = await transfer_nodes(
        client,
        tree,
        [tree.nodes[1], tree.nodes[2]],
        target_dir="/我的资源/临时",
        account_uid=2,
    )
    assert result.own_share is True
    assert result.direct_paths == ["/文件夹/a.txt", "/b.txt"]
    assert client.calls == []


async def test_transfer_nodes_without_fs_id() -> None:
    tree = make_tree()
    node = ShareNode(name="无ID", path="/无ID", is_dir=False)
    with pytest.raises(TransferError):
        await transfer_nodes(FakeTransferClient(), tree, [node], target_dir="/x")


async def test_transfer_nodes_propagates_error() -> None:
    tree = make_tree()
    with pytest.raises(TransferError):
        await transfer_nodes(
            FakeTransferClient(error=TransferError("空间不足")),
            tree,
            [tree.nodes[0]],
            target_dir="/x",
        )
