# devbase のチームの読み取りの雛形（version: 2。グループ単位）
#
# __GROUP__ をグループの名前に置き換えて使う（例: personal）。
#   sed 's/__GROUP__/personal/g' team-read.hcl
# 置き換えたグループの team/<グループ>/ の下だけを読ませる。書き込みは許さない。

path "devbase/data/team/__GROUP__/global" {
  capabilities = ["read"]
}

path "devbase/data/team/__GROUP__/projects/*" {
  capabilities = ["read"]
}
