"""Docker Compose file generation for scaled deployments"""

import copy
import os
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set,
)

from devbase.env import compose_migrate
from devbase.errors import DockerError
from devbase.log import get_logger

from .manager import get_work_volume_for_index, get_ai_volume_for_index

logger = get_logger(__name__)

# 旧 /home/ubuntu マウントは非推奨のため scale 生成時に除去する
_DEPRECATED_TARGET = '/home/ubuntu'


def get_dev_service_name() -> str:
    """Get development service name from environment variable or default to 'dev'"""
    return os.environ.get('DEV_SERVICE_NAME', 'dev')


def _rewrite_depends_on(
    service_config: Dict[str, Any],
    dev_service_name: str,
    scale: int,
) -> None:
    """Rewrite `depends_on: <dev>` references to scaled instances (dev-1, ..., dev-N).

    Supports both list form (`depends_on: [dev, mysql]`) and map form
    (`depends_on: {dev: {condition: service_healthy}}`). For scale > 1 a
    single `dev` reference is expanded to every dev-i instance so that the
    dependent service waits for all of them.
    """
    deps = service_config.get('depends_on')
    if not deps:
        return

    instance_names = [f"{dev_service_name}-{i}" for i in range(1, scale + 1)]

    if isinstance(deps, list):
        service_config['depends_on'] = [
            name
            for dep in deps
            for name in (instance_names if dep == dev_service_name else [dep])
        ]
    elif isinstance(deps, dict) and dev_service_name in deps:
        condition = deps.pop(dev_service_name)
        for name in instance_names:
            deps[name] = copy.deepcopy(condition)


@dataclass(frozen=True)
class _VolumeMount:
    """1 件の volume 定義 (文字列形式 / dict 形式) をパースした値オブジェクト。

    Docker Compose の volume は ``source:target[:options]`` の文字列と
    ``{type, source, target, ...}`` の dict の 2 通りで書かれる。``isinstance``
    を伴うパースをここへ集約し、利用側は ``target`` と :meth:`with_source`
    だけを見る。``raw`` は元のエントリで、シリアライズ時に元の形式
    (文字列 / dict) を保つために持つ。
    """

    raw: Any
    source: Optional[str] = None
    target: Optional[str] = None
    options: Optional[str] = None

    @classmethod
    def parse(cls, vol: Any) -> '_VolumeMount':
        """str / dict のエントリから生成する (どちらでもなければ属性は None)"""
        if isinstance(vol, str):
            parts = vol.split(':')
            has_target = len(parts) >= 2
            return cls(
                raw=vol,
                source=parts[0] if has_target else None,
                target=parts[1] if has_target else None,
                options=parts[2] if len(parts) >= 3 else None,
            )
        if isinstance(vol, dict):
            return cls(raw=vol, source=vol.get('source'),
                       target=vol.get('target'))
        return cls(raw=vol)

    def with_source(self, source: str) -> Any:
        """source を差し替えたエントリを元の形式 (str / dict) で返す。

        dict 形式は名前付き volume への差し替えなので type も volume に揃える。
        """
        if isinstance(self.raw, str):
            options = f":{self.options}" if self.options is not None else ""
            return f"{source}:{self.target}{options}"
        self.raw['source'] = source
        self.raw['type'] = 'volume'
        return self.raw


def _volume_target(vol: Any) -> Optional[str]:
    """Return the mount target of a volume entry (string / dict form), or None."""
    return _VolumeMount.parse(vol).target


def _replace_volume_entry_for_instance(
    vol: Any, replacements: Mapping[str, str],
) -> tuple:
    """Replace one volume entry's source when its target is in ``replacements``.

    Returns ``(entry, replaced_target)``. ``replaced_target`` is ``None``
    when the entry was left untouched.
    """
    mount = _VolumeMount.parse(vol)
    source = replacements.get(mount.target)
    if source is None:
        return vol, None
    return mount.with_source(source), mount.target


def _replace_volumes_for_instance(
    volumes: list, ai_volume: str, work_volume: str,
) -> list:
    """Replace volume mounts in a service's volumes list for a specific instance.

    /home/ubuntu mounts are skipped (deprecated).
    /persistent/ai is mapped to ai_volume.
    /work is mapped to work_volume.
    """
    replacements = {'/persistent/ai': ai_volume, '/work': work_volume}
    replaced_targets = set()
    new_volumes = []

    for vol in volumes:
        if _volume_target(vol) == _DEPRECATED_TARGET:
            continue
        entry, replaced = _replace_volume_entry_for_instance(vol, replacements)
        new_volumes.append(entry)
        if replaced is not None:
            replaced_targets.add(replaced)

    # Add missing mounts
    new_volumes.extend(
        f"{source}:{target}"
        for target, source in replacements.items()
        if target not in replaced_targets
    )
    return new_volumes


def _build_volumes_section(config: dict, scale: int) -> dict:
    """Build the volumes section for a scaled compose file."""
    # Copy original volumes (mysql, valkey, etc.) from config
    volumes: Dict[str, Any] = {
        vol_name: copy.deepcopy(vol_config) if vol_config else {}
        for vol_name, vol_config in config.get('volumes', {}).items()
    }

    # Add shared home volume (devbase_home_ubuntu) once for all instances
    volumes[get_ai_volume_for_index(1)] = {'external': True}

    # Add work volumes for each dev instance (external)
    for i in range(1, scale + 1):
        volumes[get_work_volume_for_index(i)] = {'external': True}

    return volumes


def _build_networks_section(config: dict) -> dict:
    """Build the networks section for a scaled compose file."""
    if 'networks' in config:
        return config['networks']
    return {'net': {'driver': 'bridge'}}


def _load_compose_config(compose_file: Path) -> dict:
    """Read and parse the base compose file.

    docker compose config を使わず直接読むのは、環境変数 (secrets を含み得る) の
    展開を避けるため。
    """
    if not compose_file.exists():
        raise FileNotFoundError(f"Compose file not found: {compose_file}")
    try:
        with open(compose_file, 'r') as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise DockerError(f"Failed to parse compose file: {e}")


def _mask_secret_environment(
    service: dict, secret_env_names: Sequence[str],
) -> None:
    """機密キーだけを「値なしの参照」へ置き換える (それ以外の値は残す)。

    以前は ``environment`` を丸ごと落としていたが、それでは元の ``compose.yml``
    が持つ**非機密の固定値や機能フラグ**まで消え、スケールした途端に生成コンテナ
    の挙動が変わってしまう。生成ファイルに残してはいけないのは機密の値だけなので、
    ``secret_env_names`` に挙がったキーに限って値を落とし、devbase 自身の環境変数
    から解決させる書き方へ置き換える (plan35 §4.3)。

    元の記法は尊重する。map 形式なら値を ``None`` にした map (Compose は ``KEY:``
    を「実行プロセスの環境変数から解決」と解釈する)、list 形式なら裸のキー名を
    並べた list として出力する。
    """
    # 重複を除きつつ、指定された順序は保つ
    secrets = list(dict.fromkeys(secret_env_names))
    secret_set = set(secrets)
    existing = service.get('environment')

    if existing is None:
        # 元から environment が無ければ、機密が無い限り作らない
        if secrets:
            service['environment'] = list(secrets)
        return

    if isinstance(existing, dict):
        masked = {
            key: (None if key in secret_set else value)
            for key, value in existing.items()
        }
        for name in secrets:
            masked.setdefault(name, None)
        service['environment'] = masked
        return

    if isinstance(existing, list):
        masked_list = []
        listed = set()
        for item in existing:
            if not isinstance(item, str):
                masked_list.append(item)
                continue
            name = item.split('=', 1)[0].strip()
            listed.add(name)
            # 機密キーは `KEY=value` でも `KEY` でも、値なし参照に揃える
            masked_list.append(name if name in secret_set else item)
        masked_list.extend(name for name in secrets if name not in listed)
        service['environment'] = masked_list
        return

    # map / list 以外は Compose が受け付けない書き方。手掛かりを残しつつ、
    # 機密が渡らない事故を避けるため名前の列挙で置き換える。
    logger.warning(
        "environment の形式 (%s) を解釈できないため、機密の変数名の列挙で"
        "置き換えます", type(existing).__name__)
    service['environment'] = list(secrets)


class _SecretNames:
    """機密の変数名を**由来別**に保持し、参照種別に応じた部分集合を切り出す。

    共通機密 (``$DEVBASE_ROOT/.env``) 由来とプロジェクト機密
    (``projects/<name>/.env``) 由来を分けて持つのは、サービスごとに「元々
    ``env_file`` で参照していた由来のキーだけ」を列挙するため。全件をまとめて
    渡すと、共通設定だけを読んでいた ``db`` のようなサービスにプロジェクト固有
    のトークンまで届き、元の構成より機密の範囲が広がってしまう。

    由来の内訳が分からない場合 (``global_names`` / ``project_names`` が
    ``None``) は、全キーが両方の由来を持つものとして扱う。従来どおりの動作へ
    落ちるだけで、渡し先が狭まって起動できなくなる事故は起こさない。

    既知の限界: 同じキーが共通機密とプロジェクト機密の**両方**にある場合、
    Compose は値を devbase 自身の環境変数から解決するため、実際に渡る値は
    合成後の 1 つ (プロジェクト側が優先) に決まる。したがって共通側だけを参照
    していたサービスにもプロジェクト側の値が渡る。サービスごとに違う値を渡す
    には生成ファイルへ値を書き込むしかなく、それは「生成物に機密の値を残さない」
    という本方式の前提と矛盾するため受け入れる (plan35 §7)。
    """

    def __init__(
        self,
        all_names: Sequence[str] = (),
        global_names: Optional[Sequence[str]] = None,
        project_names: Optional[Sequence[str]] = None,
    ) -> None:
        split_known = global_names is not None or project_names is not None
        globals_ = list(global_names or ())
        projects = list(project_names or ())
        # 重複を除きつつ、呼び出し側が渡した順序は保つ
        self.all: List[str] = list(dict.fromkeys(
            [*all_names, *globals_, *projects]))
        if split_known:
            self._by_target = {
                compose_migrate.TARGET_GLOBAL: set(globals_),
                compose_migrate.TARGET_PROJECT: set(projects),
            }
        else:
            everything = set(self.all)
            self._by_target = {
                compose_migrate.TARGET_GLOBAL: everything,
                compose_migrate.TARGET_PROJECT: everything,
            }

    def for_targets(self, targets: Iterable[str]) -> List[str]:
        """指定の参照種別に由来するキーだけを、全体と同じ順序で返す"""
        allowed: Set[str] = set()
        for target in targets:
            allowed |= self._by_target.get(target, set())
        return [name for name in self.all if name in allowed]


def _apply_dev_environment(service: dict, extra: Mapping[str, str]) -> None:
    """dev サービスへ devbase 由来の環境変数を載せる (PLAN32: clone プラン等)。

    ``environment`` は辞書形と ``KEY=VALUE`` のリスト形の両方が使われるため、
    元の形を保ったまま追記する。同名キーは devbase 側の値で上書きする
    (clone プランはプロジェクト設定から毎回生成される正のため)。
    """
    if not extra:
        return

    existing = service.get('environment')
    if isinstance(existing, dict):
        existing.update(extra)
        return
    if isinstance(existing, list):
        names = set(extra)
        kept = [entry for entry in existing
                if not (isinstance(entry, str)
                        and entry.split('=', 1)[0] in names)]
        service['environment'] = kept + [f"{k}={v}" for k, v in extra.items()]
        return
    if existing is None:
        service['environment'] = dict(extra)
        return

    logger.warning(
        "environment の形式 (%s) を解釈できないため、devbase の環境変数を "
        "追記できませんでした", type(existing).__name__)


def _build_dev_instance(
    dev_service: dict, dev_service_name: str, index: int,
    secret_env_names: Sequence[str] = (),
    dev_environment: Optional[Mapping[str, str]] = None,
) -> dict:
    """Build the service definition for one scaled dev instance (dev-<index>)."""
    service = copy.deepcopy(dev_service)
    service['container_name'] = f"${{COMPOSE_PROJECT_NAME}}-{dev_service_name}-{index}"

    # Insert tini as PID 1 so orphaned children are reaped (no zombies).
    # setdefault keeps an explicit `init: false` if the project set one.
    service.setdefault('init', True)

    _mask_secret_environment(service, secret_env_names)
    # 機密の伏せ字化のあとに載せる。devbase 由来の値 (clone プラン等) は機密では
    # なく、そのままコンテナへ渡す必要があるため。
    _apply_dev_environment(service, dev_environment or {})

    # Update volume mounts for /persistent/ai and /work
    ai_volume = get_ai_volume_for_index(index)
    work_volume = get_work_volume_for_index(index)
    service['volumes'] = _replace_volumes_for_instance(
        service.get('volumes', []), ai_volume, work_volume,
    )

    return service


def _build_scaled_services(
    services: dict, dev_service: dict, scale: int,
    ctx: '_ScaledComposeContext',
    secret_services: Optional[Mapping[str, Set[str]]] = None,
) -> dict:
    """Build the services section: non-dev services + dev-1..dev-N instances.

    ``secret_services`` は「サービス名 → 元々参照していた機密の種別
    (``TARGET_GLOBAL`` / ``TARGET_PROJECT``)」の対応。
    """
    scaled_services = {}
    dev_service_name = ctx.dev_service_name
    secret_names = ctx.secret_names
    receivers = dict(secret_services or {})

    # Copy non-dev services (mysql, valkey, etc.) — rewriting any
    # `depends_on: <dev>` reference to the scaled instances (dev-1..N) so
    # service_healthy chains keep working after dev is renamed.
    for service_name, service_config in services.items():
        if service_name == dev_service_name:
            continue
        copied = copy.deepcopy(service_config)
        _rewrite_depends_on(copied, dev_service_name, scale)
        # Insert tini as PID 1 so orphaned children are reaped (no zombies).
        # setdefault keeps an explicit `init: false` if the project set one.
        copied.setdefault('init', True)
        # 元々機密ファイルを env_file で参照していたサービスにだけ機密を渡す。
        # 参照が外れたあと dev だけに渡すと、DB パスワードを読んでいた db の
        # ような非 dev サービスが値を受け取れず起動に失敗する。逆に参照を
        # 持たないサービスへ注入すると、元の構成に無い変数を勝手に増やす。
        #
        # さらに、渡すのは**そのサービスが参照していた由来のキーだけ**に絞る。
        # 共通設定 (${DEVBASE_ROOT}/.env) だけを読んでいたサービスへプロジェクト
        # 固有のトークンまで列挙するのは、元の構成に無かった機密を渡すことに
        # なり、範囲の拡大にあたる。
        targets = receivers.get(service_name)
        if targets:
            _mask_secret_environment(copied, secret_names.for_targets(targets))
        scaled_services[service_name] = copied

    # dev サービスは従来どおり全件 (共通 + プロジェクト) を対象にする。
    # devbase 自身が機密を注入する前提のサービスであり、env_file を書いていない
    # 構成でも両方の機密を必要とする。
    for i in range(1, scale + 1):
        scaled_services[f'{dev_service_name}-{i}'] = _build_dev_instance(
            dev_service, dev_service_name, i, secret_names.all,
            dev_environment=ctx.dev_environment,
        )
    return scaled_services


def _env_file_ref(entry: Any) -> Optional[str]:
    """``env_file`` の 1 エントリから参照先の文字列を取り出す (短縮形 / dict 形)"""
    if isinstance(entry, dict):
        entry = entry.get('path')
    return entry if isinstance(entry, str) else None


def _resolve_env_file_path(entry: Any, base_dir: Path) -> Optional[Path]:
    """``env_file`` の 1 エントリを実パスへ解決する (解釈できなければ None)"""
    entry = _env_file_ref(entry)
    if entry is None:
        return None
    expanded = os.path.expandvars(entry)
    if '$' in expanded:
        # 未定義の変数が残っている = ここでは存在判定できない。触らずに残す。
        return None
    path = Path(expanded)
    return path if path.is_absolute() else base_dir / path


def _env_file_entries(service: dict) -> Optional[list]:
    """``env_file`` の値を list へ正規化して返す (キー自体が無ければ None)"""
    entries = service.get('env_file')
    if entries is None:
        return None
    return entries if isinstance(entries, list) else [entries]


def _is_missing_secret_env_file(entry: Any, base_dir: Path) -> bool:
    """実在しない**既知の機密** ``env_file`` 参照なら真。

    パスへ解決できない参照 (未定義の変数が残る等) と、機密以外の欠落は偽。
    """
    ref = _env_file_ref(entry)
    resolved = _resolve_env_file_path(entry, base_dir)
    return (resolved is not None and not resolved.exists()
            and ref is not None and compose_migrate.is_secret_entry(ref))


def _drop_missing_env_files(service: dict, base_dir: Path, service_name: str) -> None:
    """暗号化移行で消える機密ファイルへの ``env_file`` 参照のうち、実在しないものを落とす。

    機密を暗号化すると、それまで参照していた平文ファイルは無くなる。参照を
    残したままだと Docker Compose が起動時に落ちるため、生成する構成からは
    外す。値は環境変数として別途注入されるので失われない。

    落とす対象を :func:`_is_missing_secret_env_file` が真を返す既知の参照に
    **限る**のが要点。実在しない参照を無条件に落とすと、利用者のタイプミスや
    未配置の必須設定まで黙って成功扱いになり、本来 Compose が起動時に知らせて
    くれる構成の不備を隠してしまう。機密以外の欠落はそのまま残し、Compose に
    エラーを出させる。

    移行コマンドが ``compose.yml`` を書き換え済みなら、ここに来る時点で該当
    エントリは無い。手で書いた構成や書き換え前の状態に対する保険として働く。
    """
    entries = _env_file_entries(service)
    if entries is None:
        return

    kept = []
    for entry in entries:
        if _is_missing_secret_env_file(entry, base_dir):
            logger.info(
                "%s: 実在しない機密の env_file 参照を除きました (%s)。"
                "機密は環境変数として渡されます",
                service_name, _resolve_env_file_path(entry, base_dir))
            continue
        kept.append(entry)

    if kept:
        service['env_file'] = kept
    else:
        service.pop('env_file', None)


def _services_receiving_secrets(
    ctx: '_ScaledComposeContext',
) -> Dict[str, Set[str]]:
    """機密を渡すべきサービスと、その**参照種別**を決める。

    判定は :func:`compose_migrate.services_with_secret_env_file` に任せ、
    **パース済みの YAML ではなく生テキスト**を渡す。移行後の ``compose.yml``
    では機密の ``env_file`` 参照がコメントアウトされ、YAML からは消えている
    ため、パース結果だけでは「元々その参照から機密を受け取っていたサービス」
    を復元できない。生テキストなら有効な参照とコメントアウトされた参照の
    両方を拾える。

    返すのがサービス名の集合ではなく種別つきの対応なのは、共通設定だけを
    参照していたサービスへプロジェクト固有の機密まで渡さないため。

    dev サービスは常に両方の種別を持つものとして含める。devbase 自身が機密を
    注入する前提のサービスで、``env_file`` を書いていない構成でも機密は渡す
    必要があるため。

    生テキストを読めない場合は dev サービスだけ (全件) にフォールバックする。
    判定に失敗したことを理由に全サービスへ機密を撒くと、必要のないコンテナに
    まで認証情報を渡すことになる。
    """
    dev_service_name = ctx.dev_service_name
    compose_file = ctx.compose_file
    both = {compose_migrate.TARGET_GLOBAL, compose_migrate.TARGET_PROJECT}
    receivers: Dict[str, Set[str]] = {dev_service_name: set(both)}
    try:
        text = compose_file.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(
            "%s を読めなかったため、機密は %s サービスにのみ渡します: %s",
            compose_file, dev_service_name, e)
        return receivers
    for name, targets in compose_migrate.services_with_secret_env_file(text).items():
        receivers.setdefault(name, set()).update(targets)
    return receivers


@dataclass(frozen=True)
class _ScaledComposeContext:
    """``generate_scaled_compose`` の内部ヘルパーへ渡り回る値をまとめたもの。

    compose ファイルのパス・dev サービス名・機密の変数名・dev サービスへ載せる
    devbase 由来の環境変数は、``_SecretNames`` の生成から
    ``_services_receiving_secrets`` / ``_build_scaled_services`` 呼び出しまで
    ひとまとまりで使われる。公開シグネチャ (``generate_scaled_compose`` の引数)
    は互換性のため変えず、関数の先頭でこの値へ組み立てる。
    """

    compose_file: Path
    dev_service_name: str
    secret_names: '_SecretNames'
    dev_environment: Optional[Mapping[str, str]] = field(default=None)


def generate_scaled_compose(
    scale: int,
    compose_file: Path = None,
    dev_service_name: str = None,
    secret_env_names: Sequence[str] = (),
    global_env_names: Optional[Sequence[str]] = None,
    project_env_names: Optional[Sequence[str]] = None,
    dev_environment: Optional[Mapping[str, str]] = None,
) -> Path:
    """
    Generate scaled docker-compose file with per-instance volumes

    Args:
        scale: Number of container instances
        compose_file: Source compose file path (default: compose.yml)
        dev_service_name: Name of the development service to scale (default: from DEV_SERVICE_NAME env or 'dev')
        secret_env_names: コンテナへ列挙する機密の変数名 (全件)
        global_env_names: そのうち共通機密 (``$DEVBASE_ROOT/.env``) 由来のキー
        project_env_names: そのうちプロジェクト機密由来のキー
        dev_environment: dev サービスへ載せる devbase 由来の環境変数
            (PLAN32 の clone プラン ``DEVBASE_REPOS`` 等。機密ではない)

    非 dev サービスへは、そのサービスが元々 ``env_file`` で参照していた由来の
    キーだけを列挙する。由来の内訳が渡されない場合 (両方 ``None``) は全キーを
    両方の由来とみなす。

    Returns:
        Path to generated .docker-compose.scale.yml
    """
    ctx = _ScaledComposeContext(
        compose_file=compose_file or Path("compose.yml"),
        dev_service_name=(
            dev_service_name if dev_service_name is not None
            else get_dev_service_name()
        ),
        secret_names=_SecretNames(
            secret_env_names, global_env_names, project_env_names),
        dev_environment=dev_environment,
    )
    override_file = Path(".docker-compose.scale.yml")

    config = _load_compose_config(ctx.compose_file)

    # Extract dev service (configurable via DEV_SERVICE_NAME)
    services = config.get('services', {})
    base_dir = ctx.compose_file.resolve().parent
    for service_name, service_config in services.items():
        if isinstance(service_config, dict):
            _drop_missing_env_files(service_config, base_dir, service_name)
    dev_service = services.get(ctx.dev_service_name)
    if not dev_service:
        raise DockerError(f"No '{ctx.dev_service_name}' service found in compose file")

    secret_services = _services_receiving_secrets(ctx)

    scaled_config = {
        'services': _build_scaled_services(
            services, dev_service, scale, ctx,
            secret_services=secret_services,
        ),
        'volumes': _build_volumes_section(config, scale),
        'networks': _build_networks_section(config),
    }

    try:
        with open(override_file, 'w') as f:
            yaml.dump(
                scaled_config,
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True
            )
    except IOError as e:
        raise DockerError(f"Failed to write {override_file}: {e}")

    return override_file
