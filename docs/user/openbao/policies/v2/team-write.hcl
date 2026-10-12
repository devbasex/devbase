# devbase のチームの書き込みの雛形（version: 2。グループ単位）
#
# __GROUP__ をグループの名前に置き換えて使う（例: personal）。
#   sed 's/__GROUP__/personal/g' team-write.hcl
# 置き換えたグループの team/<グループ>/ の下へ書かせる。
# そのグループのチーム共通へ書いてよい人の entity にだけ付ける。

path "devbase/data/team/__GROUP__/global" {
  capabilities = ["create", "read", "update"]
}

path "devbase/data/team/__GROUP__/projects/*" {
  capabilities = ["create", "read", "update"]
}
