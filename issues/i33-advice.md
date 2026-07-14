結論からいうと、**Orca では「remote machine」ではなく「コンテナそのもの」を SSH target として見せる**のが一番まともです。
公式 docs 上の remote 開発の入口は **SSH worktrees** と **Remote Orca Servers** で、VS Code の Dev Containers みたいに Docker container / `devcontainer.json` を直接選ぶ方式は、私が確認した範囲では見つかりませんでした。Orca の SSH worktree は、SSH target 上で `git worktree` を作り、agent も SSH target 側で動かし、editor/diff は手元で使うモデルです。([Orca][1])

## おすすめ構成: コンテナ内で `sshd` を立てる

つまりこうします。

```text
Laptop
  └─ Orca
      └─ SSH target: orca-container
            └─ remote machine 上の Docker container
                 ├─ git
                 ├─ codex / claude / opencode など
                 ├─ repo / worktrees
                 └─ sshd
```

Orca 側から見ると、これは普通の SSH host です。Orca は Settings → SSH で target を追加でき、OpenSSH config も import できます。worktree 作成時に Local ではなく SSH target を選ぶと、remote 側で worktree 作成・agent 実行・file event 同期を行います。([Orca][1])

### 1. コンテナに `sshd`, `git`, agent CLI を入れる

例として Ubuntu ベースならこんな感じです。

```Dockerfile
# Dockerfile.orca-dev
FROM ubuntu:24.04

ARG USER=dev
ARG UID=1000
ARG GID=1000

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    openssh-server git curl ca-certificates sudo bash tini \
  && groupadd -g ${GID} ${USER} \
  && useradd -m -u ${UID} -g ${GID} -s /bin/bash ${USER} \
  && echo "${USER} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USER} \
  && mkdir -p /run/sshd /workspaces /home/${USER}/.ssh \
  && chown -R ${USER}:${USER} /workspaces /home/${USER}/.ssh \
  && chmod 700 /home/${USER}/.ssh \
  && printf '\nPasswordAuthentication no\nPubkeyAuthentication yes\nAllowTcpForwarding yes\n' >> /etc/ssh/sshd_config

COPY authorized_keys /home/dev/.ssh/authorized_keys
RUN chown dev:dev /home/dev/.ssh/authorized_keys \
  && chmod 600 /home/dev/.ssh/authorized_keys

EXPOSE 22

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/sbin/sshd", "-D", "-e"]
```

`authorized_keys` には、Orca を動かす laptop 側の公開鍵を入れます。

```bash
cp ~/.ssh/id_ed25519.pub authorized_keys
docker build -f Dockerfile.orca-dev -t orca-dev .
```

### 2. remote machine 上で container を起動する

remote machine 側で、repo/worktree を永続化したい親ディレクトリを mount します。`git worktree` が sibling directory を作る可能性があるので、repo 1 個だけでなく親ディレクトリごと mount するのが無難です。

```bash
docker run -d \
  --name orca-dev \
  --restart unless-stopped \
  -p 127.0.0.1:2222:22 \
  -v /srv/repos:/workspaces \
  orca-dev
```

この例では container の SSH port を **remote machine の localhost:2222** にだけ bind しています。外部公開しないので安全寄りです。

laptop からは SSH tunnel を張ります。

```bash
ssh -N -L 2222:127.0.0.1:2222 remote-host
```

そして laptop の `~/.ssh/config` にこう書きます。

```sshconfig
Host orca-container
  HostName 127.0.0.1
  Port 2222
  User dev
  IdentityFile ~/.ssh/id_ed25519
  StrictHostKeyChecking accept-new
```

動作確認:

```bash
ssh orca-container 'whoami; hostname; git --version; pwd'
```

ここで入れれば、Orca の **Settings → SSH** に `orca-container` を追加または import して、Test します。

### 3. Orca では「SSH target として repo/folder を開く」

Orca 側では次の流れです。

1. Settings → SSH で `orca-container` を追加。
2. repo を追加するとき、location としてその SSH target を選ぶ。
3. path は container 内の path、たとえば `/workspaces/myrepo` を指定。
4. worktree を作る。
5. agent を起動する。

Orca の recipe でも、SSH target を追加して connection test し、SSH target を location として repo を追加、または remote folder を直接開く流れになっています。実行時は remote 側で `git worktree add` が走り、agent も remote 側で動き、保存は remote filesystem に stream されます。([Orca][2])

なので、SSH endpoint が container 内なら、**worktree も agent も shell も container 内**です。これが一番 “Orca らしい” remote container 開発です。

## 避けたい: host に SSH してから `docker exec`

これは一見ラクですが、Orca 的には微妙です。

```text
Orca → SSH to remote host → terminal で docker exec
```

この形だと、Orca の file explorer / editor / diff / worktree 管理は基本的に **SSH target である host 側**を見ます。terminal だけ container に入っても、Orca の “開発環境” 全体が container になるわけではありません。docs 上も、Orca は SSH target 上に worktree を作って agent を動かすモデルです。([Orca][1])

## `ProxyCommand docker exec ... sshd -i` は現時点では注意

こういう SSH config も理屈としてはあります。

```sshconfig
Host orca-container
  HostName remote-host.example.com
  User dev
  ProxyCommand ssh remote-host.example.com docker exec -i <container> /usr/sbin/sshd -i
```

ただし Orca の GitHub issue に、まさにこの `ProxyCommand` + containerized remote environment パターンで、terminal は開けるが file upload/download/import が `SFTP is not available when using system SSH transport` で失敗する、という open issue があります。([GitHub][3])

なので今は、**Orca から見て単純な `HostName + Port` の SSH target にする**のがおすすめです。つまり、container の sshd を port publish する、Tailscale で到達させる、または上のように `ssh -L` で tunnel してから Orca は `127.0.0.1:2222` に接続する、という形です。

## port forwarding / preview はどうなる？

コンテナ内で web server を立てる場合も、SSH target が container なら相性は良いです。Orca の remote worktrees では Ports tab が remote の `/proc/net/tcp` を scan して listening port を検出し、クリックで laptop に forward できます。([Orca][1])

つまり container 内で:

```bash
npm run dev -- --host 0.0.0.0
```

みたいに立てておけば、Orca の Ports tab から拾える可能性が高いです。`sshd_config` で `AllowTcpForwarding yes` は有効にしておくのが無難です。

## もう一つの選択肢: container 内で `orca serve`

より “remote runtime 全部を container に閉じ込めたい” なら、container 内で **Remote Orca Server** を動かす手もあります。

```text
Laptop: Orca UI
  └─ connects to
Container: orca serve
  ├─ repos
  ├─ worktrees
  ├─ terminals
  └─ agent sessions
```

Remote Orca Servers は、server machine が projects / worktrees / terminals / agent sessions を所有し、client は UI として接続するモデルです。docs では beta とされていて、LAN・Tailscale・SSH forwarding・tunnel など、client/server が到達できる network path が必要とされています。([Orca][4])

container 内で Orca CLI が使えるなら、概念的にはこうです。

```bash
orca serve --port 6768 --pairing-address <laptopから到達できるhost名またはIP>
```

その後 laptop の Orca で Settings → Remote Orca Servers → Add Server から pairing URL を追加します。Remote server mode では terminal PTY、agent CLI process、worktree files が server 側にあり、laptop はそれを操作する UI になります。([Orca][4])

ただ、普通の一人開発で「remote machine 上の container 内で agent を走らせたい」くらいなら、まずは **container sshd + SSH worktree** の方がシンプルです。

## 実用チェックリスト

container 内でこれが通れば、Orca でもだいたい戦えます。

```bash
ssh orca-container 'git --version'
ssh orca-container 'cd /workspaces/myrepo && git status'
ssh orca-container 'codex --version || claude --version || opencode --version'
ssh orca-container 'cd /workspaces/myrepo && npm test'
```

加えて、container 内で GitHub への clone/push、agent CLI の認証、必要な language runtime、package manager、GPU/CUDA などが揃っているかを確認してください。Remote agent は remote/container 側の `PATH`・home directory・credentials を使うため、laptop 側に入っている CLI や token はそのままでは使われません。Remote server docs でも、agent CLI と credentials は server 側に必要だと説明されています。([Orca][4])

私ならまずこの構成にします。

```text
remote machine:
  docker container:
    sshd on :22
    repo mounted at /workspaces
    codex/claude/etc installed

remote machine:
  publishes container ssh to 127.0.0.1:2222

laptop:
  ssh -L 2222:127.0.0.1:2222 remote-host

Orca:
  SSH target = 127.0.0.1:2222
  repo path = /workspaces/<repo>
```

これで Orca から見た “remote machine” は実質 container になります。

[1]: https://www.onorca.dev/docs/ssh "SSH worktrees — Orca Docs"
[2]: https://www.onorca.dev/docs/recipes/remote-worktrees "Work on a remote machine over SSH — Orca Docs"
[3]: https://github.com/stablyai/orca/issues/7781 "[Feature]: Support file transfer over system SSH transport for ProxyCommand targets · Issue #7781 · stablyai/orca · GitHub"
[4]: https://www.onorca.dev/docs/remote-servers "Remote Orca Servers — Orca Docs"
