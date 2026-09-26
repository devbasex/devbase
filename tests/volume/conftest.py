"""volume のテストの共通の前提

``devbase up`` / ``scale`` は起動の先頭でプロジェクトの宣言を読み、``DEVBASE_ACCOUNT_GROUP``
へ置いてから構成を作る (#315)。構成の生成とボリュームの作成を単独で呼ぶテストは、その後の
状態 (グループが決まっている) を前提にする。未設定の振る舞いを見るテストは自分で消す。
"""

import pytest


@pytest.fixture(autouse=True)
def _declared_account_group(_isolate_env, monkeypatch):
    monkeypatch.setenv('DEVBASE_ACCOUNT_GROUP', 'nyle')
