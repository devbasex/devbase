# devbase の個人の雛形（version: 1 / version: 2 共通）
#
# 本人（entity の名前）の users/<本人>/ の下だけを読み書きさせる。
# 他人の users/<名前>/ と、users/ 直下の一覧は許さない。
# entity の名前は、devbase の secrets/backend.yml の openbao.user と同じ値にする。
#
# マウント名が devbase でなければ、devbase/ を書き換えてから書き込む:
#   sed 's#^path "devbase/#path "<マウント名>/#' devbase-user.hcl

# 値の読み書き（env get / env set / env edit）
path "devbase/data/users/{{identity.entity.name}}/*" {
  capabilities = ["create", "read", "update"]
}

# 参照ごとの削除と、本人の置き場の中の一覧
path "devbase/metadata/users/{{identity.entity.name}}/*" {
  capabilities = ["read", "list", "delete"]
}
