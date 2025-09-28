# -*- coding: utf-8 -*-
import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(os.environ["TG_API_ID"])
api_hash = os.environ["TG_API_HASH"]
sess = StringSession(os.environ["TG_STRING_SESSION"])

with TelegramClient(sess, api_id, api_hash) as c:
    for d in c.iter_dialogs():
        ent = d.entity
        _id  = getattr(ent, "id", None)
        uname = getattr(ent, "username", None)
        title = getattr(ent, "title", None)
        if title or uname:
            # -100 付きのmegagroup/channelはここで覚えると後で便利
            print(f"id={_id} username={uname} title={title}")