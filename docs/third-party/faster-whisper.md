# SYSTRAN/faster-whisper

- Repository: https://github.com/SYSTRAN/faster-whisper
- Pinned revision: `ed9a06cd89a93e47838f564998a6c09b655d7f43`
- License: MIT (code and CTranslate2 conversions)
- Commercial use: allowed
- Status: catalogued and licence-checked; no TrendRelay adapter yet

Whisper reimplemented on CTranslate2, roughly four times faster than the
reference implementation at the same accuracy, with 8-bit quantisation
available on both CPU and GPU. A single stream fits comfortably in 6 GB.

Why it is here: the Library already models transcripts and searches them. The
search box offers "titles, hooks, transcripts, or creators" and the transcript
half of that has nothing behind it, because `speech_text` is filled by hand
today. This is the piece that would fill it, and it is the only tool evaluated
whose licence, hardware fit and usefulness are all unambiguous.
