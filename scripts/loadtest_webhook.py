#!/usr/bin/env python3
"""
Script de teste de carga para o webhook da Evolution API no ZapBot.
Envia 20 mensagens/segundo durante 60 segundos (1.200 mensagens no total).
Inclui 10% de mensagens duplicadas para testar a idempotência transacional.
Calcula métricas de latência p50, p95, p99 e taxa de sucesso.
"""

import argparse
import json
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid


def generate_message_payload(group_jid: str, sender_phone: str, msg_id: str, text: str) -> dict:
    return {
        "event": "messages.upsert",
        "instance": "zapbot_instance",
        "data": {
            "key": {
                "remoteJid": group_jid,
                "fromMe": False,
                "id": msg_id,
                "participant": f"{sender_phone}@s.whatsapp.net",
            },
            "pushName": f"User {sender_phone[-4:]}",
            "message": {
                "conversation": text,
            },
            "messageTimestamp": int(time.time()),
        },
    }


def send_webhook(url: str, secret: str, payload: dict) -> tuple[int, float]:
    """Envia requisição POST para o webhook e retorna (status_code, latency_ms)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Secret": secret,
        },
        method="POST",
    )

    start_time = time.perf_counter()
    status_code = 500
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            status_code = resp.status
    except urllib.error.HTTPError as e:
        status_code = e.code
    except Exception:
        status_code = 599
    latency = (time.perf_counter() - start_time) * 1000.0
    return status_code, latency


def run_load_test(
    url: str,
    secret: str,
    group_jid: str,
    rate: int = 20,
    duration: int = 60,
    duplicate_ratio: float = 0.1,
):
    print("=" * 70)
    print("TESTE DE CARGA DO WEBHOOK (ZapBot - Versão 2 Enxuta)")
    print(f"URL: {url}")
    print(f"Grupo: {group_jid}")
    print(f"Taxa: {rate} req/s | Duração: {duration}s | Meta: {rate * duration} requisições")
    print(f"Taxa de duplicados: {int(duplicate_ratio * 100)}%")
    print("=" * 70)

    total_requests = rate * duration
    latencies = []
    status_counts = {}

    # Pool de 10 participantes
    senders = [f"55119999900{i:02d}" for i in range(10)]
    terms = ["urgente", "promoção", "teste de carga", "ajuda", "bom dia", "pagamento"]

    # Cache de IDs para geração de duplicatas
    sent_ids = []

    start_global = time.perf_counter()

    for second in range(duration):
        second_start = time.perf_counter()
        for _ in range(rate):
            # Decide se envia duplicado
            if sent_ids and random.random() < duplicate_ratio:
                msg_id = random.choice(sent_ids)
                text = "Mensagem duplicada de teste"
            else:
                msg_id = f"LOADTEST_{uuid.uuid4().hex[:16]}"
                sent_ids.append(msg_id)
                text = f"Mensagem teste com termo '{random.choice(terms)}' - {msg_id}"

            sender = random.choice(senders)
            payload = generate_message_payload(group_jid, sender, msg_id, text)

            status, lat = send_webhook(url, secret, payload)
            latencies.append(lat)
            status_counts[status] = status_counts.get(status, 0) + 1

        # Controle de taxa para manter exatamente N req/s
        elapsed_in_second = time.perf_counter() - second_start
        if elapsed_in_second < 1.0:
            time.sleep(1.0 - elapsed_in_second)

        current_total = len(latencies)
        p95_so_far = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0
        sys.stdout.write(
            f"\rProgresso: {second + 1}/{duration}s | Reqs: {current_total}/{total_requests} | p95 atual: {p95_so_far:.1f}ms"
        )
        sys.stdout.flush()

    total_time = time.perf_counter() - start_global
    print("\n\n" + "=" * 70)
    print("RESULTADOS DO TESTE DE CARGA")
    print("=" * 70)

    latencies.sort()
    count = len(latencies)
    p50 = latencies[int(count * 0.50)]
    p95 = latencies[int(count * 0.95)]
    p99 = latencies[int(count * 0.99)]
    avg_lat = statistics.mean(latencies)

    print(f"Tempo total: {total_time:.2f}s | Taxa efetiva: {count / total_time:.1f} req/s")
    print(f"Total de requisições enviadas: {count}")
    print(f"Status codes: {status_counts}")
    print(f"Latência Média: {avg_lat:.2f} ms")
    print(f"Latência p50:   {p50:.2f} ms")
    print(f"Latência p95:   {p95:.2f} ms (Meta: < 100 ms)")
    print(f"Latência p99:   {p99:.2f} ms")

    success_rate = (status_counts.get(200, 0) / count) * 100
    print(f"Taxa de sucesso HTTP 200: {success_rate:.2f}%")

    if p95 < 100.0 and success_rate >= 99.0:
        print("\n[SUCESSO] Critérios de aceite de performance plenamente atingidos!")
    else:
        print(
            "\n[ALERTA] Performance fora da meta esperada. Verifique locks de banco ou threads de workers."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Teste de Carga do Webhook ZapBot")
    parser.add_argument("--url", default="http://localhost:8000/webhooks/evolution/")
    parser.add_argument("--secret", default="webhook_shared_secret_min_32_characters_random")
    parser.add_argument("--group-jid", default="120363041234567890@g.us")
    parser.add_argument("--rate", type=int, default=20, help="Requisições por segundo")
    parser.add_argument("--duration", type=int, default=60, help="Duração em segundos")
    args = parser.parse_args()

    run_load_test(
        url=args.url,
        secret=args.secret,
        group_jid=args.group_jid,
        rate=args.rate,
        duration=args.duration,
    )
