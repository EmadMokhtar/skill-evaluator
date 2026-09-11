# 1Password secret references, resolved at run time by `op run --env-file=.env.tpl -- <command>`.
# This file holds addresses, not secrets, so it is safe to commit.
#
# The vault and item names below are one maintainer's. To use this file, create an
# "API Credential" item in your own 1Password and point the reference at it:
#   op://<vault>/<item title or ID>/credential
OPENAI_API_KEY=op://Private/OpenAI-API/credential
