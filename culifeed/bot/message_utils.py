"""Telegram message helpers."""

from typing import List

from telegram import Update


TELEGRAM_MAX_MESSAGE_LENGTH = 4096
SAFE_CHUNK_LENGTH = 3800


def split_long_message(text: str, max_length: int = SAFE_CHUNK_LENGTH) -> List[str]:
    if len(text) <= max_length:
        return [text]

    chunks: List[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        block = paragraph + "\n\n"
        if len(block) > max_length:
            if current.strip():
                chunks.append(current.rstrip())
                current = ""
            for i in range(0, len(block), max_length):
                chunks.append(block[i : i + max_length].rstrip())
            continue
        if len(current) + len(block) > max_length and current:
            chunks.append(current.rstrip())
            current = block
        else:
            current += block

    if current.strip():
        chunks.append(current.rstrip())
    return chunks


async def reply_long(update: Update, text: str, parse_mode: str = "Markdown") -> None:
    for chunk in split_long_message(text):
        await update.message.reply_text(chunk, parse_mode=parse_mode)  # type: ignore[union-attr]
