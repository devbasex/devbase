"""AWS のプロファイルの切り出し (#314)

``~/.aws/config`` と ``~/.aws/credentials`` から、選んだプロファイルとその連なり
(``sso_session`` が指す ``[sso-session <名前>]`` と ``source_profile`` が指すプロファイル) の
節だけを取り出し、``AWS_CONFIG_BASE64`` の値 (tar.gz の base64) を作る。

節の中身は元のファイルの行をそのまま写す (決定 7)。configparser は連なりの値を読むためだけに使う。
ファイルの中身を受け取って結果を返す純粋な処理で、出力も終了コードも持たない (I6)。
"""

import base64
import configparser
import gzip
import hashlib
import io
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: 節の見出し。AWS の CLI と SDK が使う configparser と同じ形 (``[private] trailing`` も
#: ``private`` の節になる)。境界が食い違うと、選ばなかった節の行が選んだ節へ入る
_HEADER_RE = configparser.RawConfigParser.SECTCRE
_COMMENT_PREFIXES = ('#', ';')

#: 控えの ``aws`` の項目で使うファイルの並び
SOURCE_FILES = ["~/.aws/config", "~/.aws/credentials"]


@dataclass
class Inclusion:
    """連なりで含めた節と、その理由"""
    section: str
    reason: str


@dataclass
class AwsPayload:
    """切り出した ``config`` / ``credentials`` の中身。無い側は ``None``"""
    config: Optional[bytes]
    credentials: Optional[bytes]
    included: List[Inclusion] = field(default_factory=list)
    #: 連なりの指す先がファイルに無かった名前 (含めずに続ける)
    missing: List[str] = field(default_factory=list)
    #: 選んだのにファイルに無かったプロファイル
    unknown: List[str] = field(default_factory=list)
    #: 空白を揃えると同じ名前になる、見出しの違う節が複数あった名前。切り出す節に当たれば
    #: 別の節の原文 (秘密鍵を含む) が混ざるため、中身を作らない (``config`` も ``credentials`` も ``None``)
    conflicts: List[str] = field(default_factory=list)

    def _files(self) -> List[Tuple[str, bytes]]:
        return [(name, data) for name, data in (('config', self.config),
                                                 ('credentials', self.credentials))
                if data is not None]

    def encode(self) -> Optional[str]:
        """tar.gz → base64。時刻を固定し、同じ中身なら同じ値になる。中身が無ければ ``None``"""
        files = self._files()
        if not files:
            return None
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode='wb', mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode='w') as tar:
                for name, data in files:
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o600
                    info.mtime = 0
                    tar.addfile(info, io.BytesIO(data))
        return base64.b64encode(buffer.getvalue()).decode('ascii')

    def digest(self) -> Optional[str]:
        """切り出した中身のハッシュ (控えの ``aws_profiles`` の ``hash``)"""
        files = self._files()
        if not files:
            return None
        h = hashlib.sha256()
        for name, data in files:
            h.update(name.encode('ascii') + b'\0' + str(len(data)).encode('ascii') + b'\0')
            h.update(data)
        return h.hexdigest()


def _normalize(header: str) -> str:
    return ' '.join(header.split())


def split_sections(text: str) -> Dict[str, str]:
    """節の見出し (空白を 1 つに揃えたもの) → 原文。境界の引き方は :func:`_split` にある"""
    return _split(text)[0]


def _split(text: str) -> Tuple[Dict[str, str], List[str]]:
    """節の見出し (空白を 1 つに揃えたもの) → 見出しの行から次の見出しの前までの原文

    節の境界は configparser (``RawConfigParser`` の既定) と同じに引く。見出しは前後の空白を
    除いた行が ``[名前]`` で始まるもので、値の続きの行 (値を持つキーの後の、より深く字下げされた行)
    は見出しに見えても続きとして扱う。空行は値の続きを切らない。コメントの行は値の続きを切る
    (Python 3.12 まで。3.13 は切らない)。版で割れる行は見出しとして扱い、選んだ節を狭める側へ倒す。

    2 つ目の戻り値は、空白を揃えると同じになるが元の見出しが違う節の名前 (``[dev]`` と ``[ dev ]``)。
    configparser はこれらを別の節として読むため、同じ名前の原文へ連結すると別の節の中身が混ざる。
    """
    sections: Dict[str, List[str]] = {}
    raw_headers: Dict[str, str] = {}
    conflicts: List[str] = []
    current: Optional[str] = None
    in_value = False  # 直前のキーの値が続き得るか
    indent_level = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(_COMMENT_PREFIXES):
            in_value = False
        elif not stripped:
            pass
        else:
            cur_indent = len(line) - len(line.lstrip())
            if current is not None and in_value and cur_indent > indent_level:
                pass  # 値の続き
            else:
                indent_level = cur_indent
                match = _HEADER_RE.match(stripped)
                if match:
                    raw = match.group('header')
                    current = _normalize(raw)
                    if raw_headers.setdefault(current, raw) != raw and current not in conflicts:
                        conflicts.append(current)
                    sections.setdefault(current, [])
                    in_value = False
                else:
                    in_value = current is not None
        if current is not None:
            sections[current].append(line)
    return {name: ''.join(lines) for name, lines in sections.items()}, conflicts


def _config_section(sections: Dict[str, str], profile: str) -> Optional[str]:
    """``~/.aws/config`` の中でプロファイルの節の見出しを探す"""
    candidates = ['default', 'profile default'] if profile == 'default' else [
        f'profile {profile}', profile]
    for name in candidates:
        if name in sections:
            return name
    return None


def profile_names(config_text: Optional[str]) -> List[str]:
    """``config`` のプロファイル名 (``[default]`` と ``[profile <名前>]``。現れた順)"""
    names: List[str] = []
    for header in split_sections(config_text or ''):
        if header == 'default':
            name = 'default'
        elif header.startswith('profile '):
            name = header[len('profile '):].strip()
        else:
            continue
        if name and name not in names:
            names.append(name)
    return names


def credential_names(credentials_text: Optional[str]) -> List[str]:
    return list(split_sections(credentials_text or ''))


def candidate_names(config_text: Optional[str], credentials_text: Optional[str]) -> List[str]:
    """取り込みの候補。``config`` が無く ``credentials`` だけなら、その節名"""
    if config_text is not None:
        return profile_names(config_text)
    return credential_names(credentials_text)


def _values(section_text: str) -> Dict[str, str]:
    """1 つの節の原文からキーと値を読む (見出しの空白の揺れに左右されない)"""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(section_text)
    except configparser.Error:
        return {}
    names = parser.sections()
    if not names:
        return {}
    return {k: v.strip() for k, v in parser[names[0]].items()}


def build(config_text: Optional[str], credentials_text: Optional[str],
          selected: Sequence[str]) -> AwsPayload:
    """選んだプロファイルとその連なりの節だけを切り出す"""
    config_sections, config_conflicts = _split(config_text or '')
    cred_sections, cred_conflicts = _split(credentials_text or '')
    config_out: List[str] = []
    cred_out: List[str] = []
    payload = AwsPayload(None, None)
    seen_profiles: List[str] = []
    seen_sessions: List[str] = []

    def add_profile(name: str, reason: Optional[str]) -> bool:
        if name in seen_profiles:
            return True
        header = _config_section(config_sections, name)
        in_creds = name in cred_sections
        if header is None and not in_creds:
            return False
        seen_profiles.append(name)
        if reason:
            shown = header if header is not None else name
            payload.included.append(Inclusion(shown, reason))
        if header is not None:
            config_out.append(header)
        if in_creds:
            cred_out.append(name)
        queue.append((name, header))
        return True

    queue: List[Tuple[str, Optional[str]]] = []
    for name in selected:
        if not add_profile(name, None):
            payload.unknown.append(name)
    while queue:
        name, header = queue.pop(0)
        if header is None:
            continue
        values = _values(config_sections[header])
        label = 'default' if header in ('default', 'profile default') else f'profile {name}'
        session = values.get('sso_session')
        if session and session not in seen_sessions:
            session_header = _normalize(f'sso-session {session}')
            if session_header in config_sections:
                seen_sessions.append(session)
                config_out.append(session_header)
                payload.included.append(Inclusion(session_header, f'{label} の sso_session'))
            else:
                payload.missing.append(session_header)
        source = values.get('source_profile')
        if source and not add_profile(source, f'{label} の source_profile'):
            payload.missing.append(f'profile {source}')

    payload.conflicts = ([h for h in config_out if h in config_conflicts]
                         + [h for h in cred_out if h in cred_conflicts])
    if payload.conflicts:
        return payload
    if config_out:
        payload.config = ''.join(_ensure_newline(config_sections[h]) for h in config_out).encode('utf-8')
    if cred_out:
        payload.credentials = ''.join(_ensure_newline(cred_sections[h])
                                      for h in cred_out).encode('utf-8')
    return payload


def _ensure_newline(text: str) -> str:
    return text if text.endswith('\n') else text + '\n'


def read_home(home: Optional[Path] = None) -> Tuple[Optional[str], Optional[str]]:
    """``~/.aws/config`` と ``~/.aws/credentials`` の中身 (無ければ ``None``)"""
    aws_dir = (home or Path.home()) / '.aws'

    def read(name: str) -> Optional[str]:
        path = aws_dir / name
        return path.read_text(encoding='utf-8') if path.is_file() else None

    return read('config'), read('credentials')


def build_from_home(selected: Sequence[str], home: Optional[Path] = None) -> AwsPayload:
    config_text, credentials_text = read_home(home)
    return build(config_text, credentials_text, selected)


def profiles_in_value(value: Optional[str]) -> Optional[List[str]]:
    """``AWS_CONFIG_BASE64`` の値に入っているプロファイルの名前。値が読めなければ ``None``"""
    if not value:
        return None
    try:
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(value)), mode='r:*') as tar:
            files = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    except (ValueError, tarfile.TarError, OSError, AttributeError, EOFError):
        return None
    try:
        config = files['config'].decode('utf-8') if 'config' in files else None
        credentials = (files['credentials'].decode('utf-8')
                       if 'credentials' in files else None)
    except UnicodeDecodeError:
        return None
    names = profile_names(config)
    names += [n for n in credential_names(credentials) if n not in names]
    return names
