# devbase のチームの書き込みの雛形（version: 1）
#
# チーム共通（team/global）とチームのプロジェクト（team/projects/<名前>）へ書かせる。
# チーム共通へ書いてよい人の entity にだけ付ける。読み取りは team-read.hcl が持つ。

path "devbase/data/team/global" {
  capabilities = ["create", "read", "update"]
}

path "devbase/data/team/projects/*" {
  capabilities = ["create", "read", "update"]
}
