"""表示幅で桁を揃える関数 (#244)"""

from __future__ import annotations

from devbase.utils.text_width import column_width, display_width, pad


def test_wide_characters_count_as_two_and_others_as_one():
    assert display_width('abc') == 3
    assert display_width('グループ') == 8
    assert display_width('（）') == 4            # 全角の括弧は F
    assert display_width('→') == 1              # 曖昧幅 A は 1


def test_pad_fills_to_the_display_width():
    assert pad('ab', 5) == 'ab   '
    assert display_width(pad('グループ a', 16)) == 16


def test_pad_leaves_an_overflowing_text_as_it_is():
    assert pad('long-name', 4) == 'long-name'


def test_column_width_is_the_wider_of_the_minimum_and_the_widest_text():
    assert column_width(['a', 'bb'], 4) == 4
    assert column_width(['a', 'プロジェクト'], 4) == 12
    assert column_width([], 3) == 3
