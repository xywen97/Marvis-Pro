"""
快速排序（quicksort）实现。

职责：
1. 提供 `quicksort(items, *, key=None, reverse=False)`，返回排序后的新列表。
2. 直接运行时执行内置自测（示例数据 + 断言），不做任何外部依赖。

设计：
- 返回新列表（非原地排序），入参不会被修改。
- 三路划分（Dutch national flag）+ 取首/中/尾中位数作为基准：
  大量重复元素时依然高效，最坏情况的退化也得到缓解。
- 小区间（长度 < 16）改用插入排序，减少递归开销。
- 先处理较短的一侧、较长的一侧用循环消除尾递归，递归深度保持 O(log n)。
- 边界情况：空列表、单元素、已排序、逆序、全部重复元素、负数与字符串等均可处理。
- `key` 与 `reverse` 语义与内置 `sorted()` 一致；不保证稳定性（快排本身不稳定）。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, List, Optional, Tuple

__all__ = ["quicksort"]

# 区间长度小于该阈值时改用插入排序。
_INSERTION_THRESHOLD = 16


def quicksort(
    items: Iterable[Any],
    *,
    key: Optional[Callable[[Any], Any]] = None,
    reverse: bool = False,
) -> List[Any]:
    """返回把 `items` 按升序（`reverse=True` 时为降序）排列后的新列表。

    Args:
        items: 任意可迭代对象；不会被修改。
        key: 排序关键字函数，默认为元素本身。
        reverse: 为 True 时按降序排列。

    Returns:
        排序后的新列表。

    Examples:
        >>> quicksort([3, 1, 2])
        [1, 2, 3]
        >>> quicksort([3, 1, 2], reverse=True)
        [3, 2, 1]
        >>> quicksort([])
        []
    """
    data: List[Any] = list(items)
    if len(data) < 2:
        return data

    keyfunc: Callable[[Any], Any] = key if key is not None else _identity
    _sort_range(data, 0, len(data) - 1, keyfunc, reverse)
    return data


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _identity(value: Any) -> Any:
    """默认关键字函数：元素自身。"""
    return value


def _key_less(left: Any, right: Any, reverse: bool) -> bool:
    """比较两个已计算出的关键字，返回 left 是否应排在 right 前面。"""
    return left > right if reverse else left < right


def _sort_range(
    data: List[Any],
    lo: int,
    hi: int,
    keyfunc: Callable[[Any], Any],
    reverse: bool,
) -> None:
    """就地对闭区间 [lo, hi] 排序（迭代处理较长一侧，递归处理较短一侧）。"""
    while lo < hi:
        if hi - lo < _INSERTION_THRESHOLD:
            _insertion_sort(data, lo, hi, keyfunc, reverse)
            return

        pivot = _median_of_three(data, lo, hi, keyfunc, reverse)
        lt, gt = _partition(data, lo, hi, pivot, keyfunc, reverse)

        # 先递归较短的一侧，保持递归深度 O(log n)。
        if lt - lo < hi - gt:
            _sort_range(data, lo, lt - 1, keyfunc, reverse)
            lo = gt + 1
        else:
            _sort_range(data, gt + 1, hi, keyfunc, reverse)
            hi = lt - 1


def _median_of_three(
    data: List[Any],
    lo: int,
    hi: int,
    keyfunc: Callable[[Any], Any],
    reverse: bool,
) -> Any:
    """返回 data[lo]、data[mid]、data[hi] 三个元素按当前序的中位数元素。"""
    mid = (lo + hi) // 2
    a, b, c = data[lo], data[mid], data[hi]
    if _key_less(keyfunc(b), keyfunc(a), reverse):
        a, b = b, a
    if _key_less(keyfunc(c), keyfunc(b), reverse):
        b, c = c, b
        if _key_less(keyfunc(b), keyfunc(a), reverse):
            a, b = b, a
    return b


def _partition(
    data: List[Any],
    lo: int,
    hi: int,
    pivot: Any,
    keyfunc: Callable[[Any], Any],
    reverse: bool,
) -> Tuple[int, int]:
    """三路划分：返回 (lt, gt)，使 [lo, lt-1] 小于基准、[lt, gt] 等于基准、[gt+1, hi] 大于基准。"""
    pivot_key = keyfunc(pivot)
    lt, i, gt = lo, lo, hi
    while i <= gt:
        current_key = keyfunc(data[i])
        if _key_less(current_key, pivot_key, reverse):
            data[lt], data[i] = data[i], data[lt]
            lt += 1
            i += 1
        elif _key_less(pivot_key, current_key, reverse):
            data[i], data[gt] = data[gt], data[i]
            gt -= 1
        else:
            i += 1
    return lt, gt


def _insertion_sort(
    data: List[Any],
    lo: int,
    hi: int,
    keyfunc: Callable[[Any], Any],
    reverse: bool,
) -> None:
    """对小区间 [lo, hi] 做插入排序。"""
    for i in range(lo + 1, hi + 1):
        current = data[i]
        current_key = keyfunc(current)
        j = i - 1
        while j >= lo and _key_less(current_key, keyfunc(data[j]), reverse):
            data[j + 1] = data[j]
            j -= 1
        data[j + 1] = current


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------


def _self_test() -> None:
    """内置自测：示例数据 + 断言。"""
    import random

    # 边界情况：空列表、单元素
    assert quicksort([]) == []
    assert quicksort([42]) == [42]

    # 常规示例
    example = [3, 6, 1, 8, 2, 9, 4]
    print(f"输入:   {example}")
    print(f"升序:   {quicksort(example)}")
    print(f"降序:   {quicksort(example, reverse=True)}")
    assert quicksort(example) == [1, 2, 3, 4, 6, 8, 9]
    assert quicksort(example, reverse=True) == [9, 8, 6, 4, 3, 2, 1]

    # 不修改入参
    assert example == [3, 6, 1, 8, 2, 9, 4]

    # 重复元素、已排序、逆序、负数
    assert quicksort([5, 5, 5, 5]) == [5, 5, 5, 5]
    assert quicksort([2, 1, 2, 1, 2]) == [1, 1, 2, 2, 2]
    assert quicksort([1, 2, 3, 4, 5]) == [1, 2, 3, 4, 5]
    assert quicksort([5, 4, 3, 2, 1]) == [1, 2, 3, 4, 5]
    assert quicksort([-3, 0, -10, 7, 0]) == [-10, -3, 0, 0, 7]

    # 字符串
    assert quicksort(["banana", "apple", "cherry"]) == ["apple", "banana", "cherry"]

    # key 参数：按元组第二项排序
    pairs = [(1, "b"), (3, "a"), (2, "c")]
    assert quicksort(pairs, key=lambda item: item[1]) == [(3, "a"), (1, "b"), (2, "c")]

    # 随机数据与内置 sorted() 对照，含大量重复值
    rng = random.Random(20261013)
    for size in (0, 1, 2, 17, 33, 200, 1000):
        for _ in range(5):
            plain = [rng.randint(-20, 20) for _ in range(size)]
            assert quicksort(plain) == sorted(plain), plain
            assert quicksort(plain, reverse=True) == sorted(plain, reverse=True), plain
            unique = rng.sample(range(-10_000, 10_000), min(size, 1000))
            assert quicksort(unique) == sorted(unique), unique

    # 深递归场景：极不平衡的数据也不应触发 RecursionError
    assert quicksort(list(range(5000))) == list(range(5000))

    print("全部自测通过。")


if __name__ == "__main__":
    _self_test()
