"""複数の破壊的な操作を「途中失敗で不整合を残さない」単位にまとめる仕組み

機密の移行 (``env encrypt`` / ``decrypt``) も受信者の更新 (``env rekey``) も、
複数のファイルを書き換えて初めて意味を持つ操作である。「全部検証してから全部
実行する」とフェーズを分けるだけでは、実行フェーズの途中で失敗したぶんが
中間状態として残る。そこで **操作を 1 つ実行するたびにその取り消し手続きを積み**、
どこで失敗しても逆順に巻き戻せるようにする。

実装は元々 ``commands/env_migrate`` の内部クラスだったが、``env rekey`` でも
同じ保証が要る (受信者リストだけ更新され、暗号文の一部が旧受信者宛のまま残ると、
自分の鍵を外す操作では再実行すらできなくなる) ため、共有モジュールへ移した。
"""

from __future__ import annotations

from typing import Callable, List, Tuple

from devbase.log import get_logger

logger = get_logger(__name__)


class Rollback:
    """実行した操作の取り消し手続きを積み、失敗時に逆順で実行する。

    使う側は「破壊的な操作をできるだけ後ろへ寄せる」ことと合わせて設計する。
    先に失うものが少ない操作から実行しておけば、途中で失敗しても巻き戻しは
    「作ったものを消す」だけで済み、復旧の余地が広く残る。
    """

    def __init__(self) -> None:
        self._undo: List[Tuple[str, Callable[[], None]]] = []

    def push(self, description: str, undo: Callable[[], None]) -> None:
        """実行済みの操作に対する取り消し手続きを積む。

        Args:
            description: 取り消しが何をするか (巻き戻しに失敗したときに
                「何が残っているか」として利用者へ見せる)
            undo: 取り消し手続き
        """
        self._undo.append((description, undo))

    def unwind(self) -> None:
        """積んだ取り消し手続きを逆順に実行する。

        後の操作は前の操作を前提にしているため、必ず逆順で戻す。巻き戻しの
        途中で失敗しても残りは試みるが、**握り潰さずに何が残っているかを
        具体的に列挙する**。ここで黙ると、利用者は壊れた状態に気付けない。
        """
        failures: List[str] = []
        for description, undo in reversed(self._undo):
            try:
                undo()
            except Exception as e:  # 1 つ失敗しても残りの巻き戻しは続ける
                failures.append(f"  - {description}: {e}")
        self._undo.clear()
        if failures:
            logger.error(
                "巻き戻しに失敗しました。次の操作が完了しておらず、"
                "手動での復旧が必要です:\n%s", "\n".join(failures))
