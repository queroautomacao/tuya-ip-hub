<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright (C) 2026 Quero Automação Ltda -->

# Relay (Relay do acesso remoto)

The rendezvous of the temporary remote access. Hubs dial in from inside the network of a
customer; a browser reaches one of them by an address that lives as long as that hub's window.
It authenticates no hub and decides nothing about any customer: the address is the secret, and
the window, the code of the window, the password and the second factor are checked by the hub
at the other end of the socket. It holds nothing on disk.

Run it behind a reverse proxy that terminates TLS. `RELAY_BASE` is the public address, and it
is what a hub publishes to the app of its customer.

## The registry of homes

The code of the hub is public, so the thing that keeps this service for the installations of
the maker cannot live in the hub: it lives here, as a question to the **maker's own registry**.
When a hub dials in, its first frame says which home of the app it belongs to (the maker's
miniApp writes that home to the hub once, on DP 184); the relay asks Directus whether a record
with that home exists and only then hands out an address. A hub of a home nobody registered
is closed with code `4403` and waits ten minutes before trying again; a registry that cannot
be reached closes with `4503`, and the hub dials again in a moment.

The question is one GET on `/items/<collection>?filter[<field>][_eq]=<home>&limit=1`, with a
static token. Give that token a role that can read that one collection and nothing else; a
permission filter on the role (say, only records of a company of the right kind) is how the
maker decides which homes count, without touching this service. A yes is remembered for five
minutes; a no is asked again on every dial, so a home registered a moment ago counts at once.

```
RELAY_DIRECTUS_URL=http://directus:8055
RELAY_DIRECTUS_TOKEN=<token of the read only role>
RELAY_DIRECTUS_COLECAO=homes
RELAY_DIRECTUS_CAMPO=home_id
```

Without the first two the relay serves ANY hub that dials, which is right on a bench and
wrong on the internet.

Beside a Traefik that already runs on the server, as a service of the same compose file:

```yaml
  iphub-relay:
    build:
      context: ./tuya-ip-hub
      dockerfile: relay/Dockerfile
    container_name: iphub-relay
    restart: always
    environment:
      RELAY_BASE: https://remoto.exemplo.com.br
      # Every peer of a service with no published port is the proxy of the same compose, and
      # the private ranges say exactly that. Without it the whole fleet would be counted as
      # one address, and so would every browser on the internet.
      RELAY_PROXIES: 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
      RELAY_DIRECTUS_URL: http://directus:8055
      RELAY_DIRECTUS_TOKEN: ${RELAY_DIRECTUS_TOKEN}
      RELAY_DIRECTUS_COLECAO: homes
      RELAY_DIRECTUS_CAMPO: home_id
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.iphubrelay.rule=Host(`remoto.exemplo.com.br`)"
      - "traefik.http.routers.iphubrelay.tls=true"
      - "traefik.http.routers.iphubrelay.entrypoints=web,websecure"
      - "traefik.http.routers.iphubrelay.tls.certresolver=mytlschallenge"
      - "traefik.http.services.iphubrelay.loadbalancer.server.port=8090"
      - "traefik.http.middlewares.iphubrelay.headers.STSSeconds=315360000"
      - "traefik.http.middlewares.iphubrelay.headers.forceSTSHeader=true"
      - "traefik.http.middlewares.iphubrelay.headers.STSIncludeSubdomains=true"
      - "traefik.http.routers.iphubrelay.middlewares=iphubrelay"
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

No port is published: Traefik reaches it over the network of the compose project. Point the
fleet at it with `IPHUB_REMOTO_RELAY=https://remoto.exemplo.com.br` on each hub.

---

O encontro do acesso remoto temporário. Os hubs discam de dentro da rede do cliente; um
navegador alcança um deles por um endereço que dura o que a janela daquele hub durar. Ele não
autentica hub nenhum e não decide nada sobre cliente nenhum: o endereço é o segredo, e a
janela, o código da janela, a senha e o segundo fator são conferidos pelo hub, do outro lado do
socket. Não guarda nada em disco.

Rode atrás de um proxy reverso que termine o TLS. O `RELAY_BASE` é o endereço público, e é o
que um hub publica no aplicativo do cliente dele.

O código do hub é público, então o que mantém este serviço para as instalações do fabricante
não pode morar no hub: mora aqui, como uma pergunta ao **cadastro do próprio fabricante**.
Quando um hub disca, o primeiro quadro diz a que Home do aplicativo ele pertence (o miniApp do
fabricante grava esse Home no hub uma vez, no DP 184); o relay pergunta ao Directus se existe
um registro com aquele Home e só então entrega um endereço. Hub de Home que ninguém cadastrou é
fechado com o código `4403` e espera dez minutos para tentar de novo; cadastro fora do ar fecha
com `4503`, e o hub disca de novo em instantes. A pergunta é um GET em
`/items/<coleção>?filter[<campo>][_eq]=<home>&limit=1`, com um token estático: dê a esse
token um papel que lê só essa coleção, e um filtro de permissão no papel (por exemplo, só
registros de empresa do tipo certo) é como o fabricante decide quais Homes contam, sem mexer
neste serviço. Um sim é lembrado por cinco minutos; um não é perguntado de novo a cada
discagem, então um Home cadastrado agora há pouco conta na hora. Sem `RELAY_DIRECTUS_URL` e
`RELAY_DIRECTUS_TOKEN` o relay atende QUALQUER hub que discar, o que é certo na bancada e
errado na internet.

Dois limites que importam quando a frota cresce: um endereço público segura 64 hubs ao mesmo
tempo (as operadoras põem muitos clientes atrás de um endereço), e um processo segura 20 000
sockets. Os dois respondem `429 hubs_demais` ao hub, que espera e disca de novo.
