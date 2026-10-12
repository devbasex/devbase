# devbase のチームの読み取りの雛形（version: 1）
#
# チーム共通（team/global）とチームのプロジェクト（team/projects/<名前>）を読ませる。
# 書き込みは許さない。書かせる人には team-write.hcl も付ける。

path "devbase/data/team/global" {
  capabilities = ["read"]
}

path "devbase/data/team/projects/*" {
  capabilities = ["read"]
}
