"""WAVE 4 — Multimodal interaction loop (Voice+Vision+Computer+Browser).

Additive over frozen Waves 1-3. Mevcut bileşenler (app/voice/*, app/vision/*,
app/browser/*, app/automation/*, app/orchestr/*) DEĞİŞTİRİLMEZ; burada
birleştirici runtime'lar ve yeni sözleşmeler tanımlanır.

Modüller (commit sırasıyla eklenir):
  voice_runtime.py — ses durum makinesi + paralel streaming pipeline +
                     latency ölçümü (P50/P95/P99)
"""
