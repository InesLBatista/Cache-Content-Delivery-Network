Este documento descreve, por ordem de prioridade, as melhorias a implementar no projeto CDN existente.  
O objectivo é evoluir de um protótipo funcional para uma plataforma tolerante a falhas, com alta concorrência, purgas garantidas, cache gerida, monitorização e escalabilidade geográfica.

## Estado atual (base do projecto)
- Origin Server funcional (aiohttp + publicador MQTT)
- CDN Node funcional (aiohttp + cache com aiofiles)
- Cliente MQTT básico no CDN (recebe purges)
- ~~Persistência planeada mas **ainda não existe ficheiro docker-compose.yml** nem volumes configurados~~
- ~~Não há contentorização efectiva~~
- **Fase 0 concluída** — sistema containerizado, seguro e resiliente a falhas de rede

---

## Fase 0 – Correções imediatas (estabilizar a base) ✅
Implementar primeiro, antes de qualquer funcionalidade nova.

### 0.1 Criar a orquestração com Docker e volumes persistentes ✅

~~O sistema actual não tem Docker.~~ Criado `docker-compose.yml` na raiz com três serviços: `mqtt-broker` (eclipse-mosquitto:2.0), `origin` e `cdn-node`. Cada serviço tem o seu `Dockerfile` baseado em `python:3.11-slim`. O volume `cdn_cache` é persistente (named volume) — o cache sobrevive a reinícios. O broker usa `mqtt_data` e `mqtt_logs` para persistência. Configuração do Mosquitto em `mosquitto/mosquitto.conf`.

### 0.2 Alinhar portas e variáveis de ambiente ✅

~~O CDN Node esperava a origem em `http://origin:8080`~~ — corrigido para `http://origin:8000`. As variáveis `ORIGIN_URL`, `MQTT_BROKER` e `CDN_PORT` estão definidas explicitamente no `docker-compose.yml`.

### 0.3 Reforçar a segurança contra path traversal ✅

Implementada a função `_safe_filename()` em `cdn_node/main.py`. Rejeita nomes com `..`, que comecem por `/` ou `\`, ou que contenham `\`. Adicionalmente, resolve o caminho com `os.path.realpath` e confirma que está dentro do `CACHE_DIR` — devolve **403 Forbidden** em caso de violação.

### 0.4 Adicionar timeout e retry no pedido à origem ✅

Implementada a função `_fetch_from_origin()` em `cdn_node/main.py`. Timeout de ligação: 5 s; timeout total: 10 s. Até 3 tentativas com backoff exponencial (1 s → 2 s → 4 s). Erros de rede devolvem **503 Service Unavailable**; respostas de erro da origem devolvem **502 Bad Gateway**.

---

## Fase 1 – Concorrência robusta (evitar o efeito “thundering herd”)

**Problema:** Se 1000 clientes pedirem o mesmo ficheiro que não está em cache, o CDN faria 1000 pedidos idênticos à origem, sobrecarregando-a.

**Solução:** Implementar um padrão de “coalescing” (singleflight). O primeiro pedido para um ficheiro inicia o download; os pedidos seguintes para o mesmo ficheiro aguardam pelo mesmo resultado. Isto é feito mantendo um dicionário de futuros pendentes por nome de ficheiro. Quando o download termina (sucesso ou falha), o futuro é removido. Esta lógica substitui a simples verificação `if exists`.

---

## Fase 2 – Fiabilidade do MQTT (purge garantido)

O sistema actual usa QoS 0 e sessão não persistente, o que pode perder mensagens de purge se o CDN estiver temporariamente offline.

### 2.1 Usar QoS 1 e sessão persistente

No publicador (Origin), a chamada publish deve incluir qos=1. No subscritor (CDN), ao criar o cliente MQTT deve passar `clean_session=False` para que o broker guarde as mensagens não entregues. Na callback de conexão, a subscrição deve também pedir qos=1.

### 2.2 Configurar reconexão automática

O cliente MQTT deve tentar religar-se ao broker automaticamente se a ligação cair, com atraso progressivo. A biblioteca paho-mqtt já suporta `reconnect_delay_set()`; deve ser configurada com atrasos mínimos e máximos.

### 2.3 Adicionar mensagem de última vontade (will)

Definir um testamento que o broker publique se o CDN se desligar inesperadamente. Útil para monitorização e para que outros componentes saibam que aquele nó está inactivo.

---

## Fase 3 – Escalabilidade horizontal (múltiplos nós geográficos)

Para reduzir a latência real, a CDN deve ser composta por vários nós, cada um com o seu próprio volume de cache. Podem ser simulados no mesmo docker-compose.yml com nomes diferentes (ex: `cdn-node-lisboa`, `cdn-node-porto`). Cada nó expõe uma porta diferente no host. É necessário colocar um balanceador de carga à frente (HAProxy ou Nginx) que distribua os pedidos dos clientes. Para simular geodistribuição, o balanceador pode usar o endereço IP do cliente para o direccionar para o nó mais próximo.

---

## Fase 4 – Gestão avançada da cache (tamanho máximo, LRU, TTL)

Actualmente o cache pode crescer sem limites. Isto leva a esgotamento do disco.

### 4.1 Limitar o tamanho total da cache

O CDN deve definir um limite máximo (ex: 10 GB). Periodicamente (ou sempre que um novo ficheiro é escrito), verifica-se o espaço ocupado. Se ultrapassar o limite, removem-se os ficheiros menos acedidos (LRU) até ficar abaixo do limite. Para tal, mantém-se um registo dos últimos acessos (pode ser guardado num ficheiro ou numa base de dados ligeira como SQLite).

### 4.2 Tempo de vida (TTL) como fallback

Mesmo com purges, um ficheiro pode ficar desactualizado se a origem falhar a enviar a mensagem. Deve ser suportado o cabeçalho `Cache-Control: max-age=...` vindo da origem. O CDN deve armazenar a data de expiração e, ao servir o ficheiro, verificar se ainda é válido. Se expirou, trata como cache miss.

---

## Fase 5 – Monitorização e observabilidade

Sem métricas, é impossível saber se a CDN está saudável.

### 5.1 Expor um endpoint /metrics

Adicionar no CDN Node um endpoint que devolva métricas no formato Prometheus: número total de pedidos (com labels hit/miss), latências, tamanho da cache, erros. Usar a biblioteca `prometheus_client`.

### 5.2 Logs estruturados em JSON

Configurar o módulo `logging` para gerar linhas em formato JSON, contendo timestamp, nível, mensagem e contexto. Isto permite integração com ferramentas como ELK ou Loki.

### 5.3 Endpoints de saúde

Implementar `/health` (indica se o processo está vivo) e `/ready` (indica se o cache está acessível e a ligação MQTT está activa). Útil para orquestradores (Kubernetes, Docker Swarm).

---

## Fase 6 – Segurança e robustez operacional

### 6.1 Rate limiting por IP

Para evitar ataques de exaustão ou tráfego excessivo de um único cliente, implementar um limitador de taxa baseado em IP (ex: máximo 100 pedidos por minuto). Pode ser feito com um dicionário em memória ou com a biblioteca `aiohttp-middlewares`.

### 6.2 Autenticação no MQTT

O broker MQTT não deve aceitar ligações anónimas. Criar um utilizador e palavra-passe para a origem e para os CDNs. Configurar o mosquitto com ficheiro de credenciais.

### 6.3 HTTPS em produção

Em ambiente real, todo o tráfego deve ser cifrado. Colocar um reverse proxy (Nginx) à frente da CDN e da origem, com certificados Let's Encrypt.

### 6.4 Validação profunda dos purges

O CDN deve verificar se o nome do ficheiro recebido no purge corresponde a um caminho dentro da cache (evitar que um purge malicioso apague ficheiros fora da cache). Rejeitar caminhos com `..` ou absolutos.

---

## Fase 7 – Testes de carga e resiliência

Ferramentas como Locust ou k6 devem ser usadas para simular milhares de clientes e validar o comportamento sob stress.

### Cenários a testar

- **Cache hit puro** – a latência deve ser inferior a 10 ms.
- **Cache miss concorrente** – apenas um pedido chega à origem (graças ao singleflight).
- **Purge durante acessos** – enviar um purge enquanto há pedidos ao mesmo ficheiro; garantir que o ficheiro é removido e que os próximos pedidos vão buscar a nova versão.
- **Falha da origem** – o CDN deve responder com 503 e não bloquear outros pedidos.
- **Falha do broker MQTT** – o CDN continua a servir cache; quando o broker recupera, a sessão persistente entrega as mensagens pendentes e os purges são aplicados.

---

## Fase 8 – Automatização e documentação

### 8.1 Makefile

Criar um `Makefile` com comandos úteis: `make build` (constrói as imagens), `make up` (sobe todos os serviços), `make down` (para e remove), `make logs` (mostra logs), `make test` (corre testes unitários).

### 8.2 Documentação actualizada

O `README.md` deve conter:
- Diagrama de arquitectura actualizado (com broker, origem, múltiplos CDNs, balanceador).
- Instruções passo-a-passo para executar em desenvolvimento com Docker.
- Exemplo de como testar o purge (usando `curl` para o endpoint `/purge`).
- Lista de variáveis de ambiente configuraveis.

### 8.3 Testes unitários e de integração

Escrever testes para o `cache_manager` (escrita/leitura/purge), para a lógica de singleflight, e para a integração MQTT (usando um broker de testes como `paho-mqtt` mock).

---

## Impacto final esperado

| Melhoria | Benefício |
|----------|------------|
| Docker + volumes | Cache persistente, ambiente reproduzível. |
| Singleflight | Redução de carga na origem em 99% em cenários de cache miss concorrente. |
| MQTT QoS 1 + sessão persistente | Garantia de que todos os nós invalidam o cache, mesmo após desconexões. |
| Múltiplos nós + balanceador | Menor latência para utilizadores geograficamente dispersos. |
| Gestão de cache (LRU, TTL) | Evita falhas por disco cheio e serve como fallback para purgas falhadas. |
| Métricas + logs estruturados | Permite detectar lentidão, erros e planear capacidade. |
| Rate limiting + autenticação MQTT | Protege contra abusos e acessos não autorizados. |
| Testes de carga | Valida que o sistema aguenta o tráfego esperado. |

Com estas implementações graduais, a CDN deixará de ser um protótipo e tornar-se-á uma plataforma **mega fiável**, pronta para produção, com elevada concorrência e tolerância a falhas.