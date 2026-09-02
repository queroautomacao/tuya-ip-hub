# Security Policy

[English](#english) | [Português](#português)

## English

### Reporting a vulnerability

Do not open a public issue or pull request for a security problem. Report it privately through the repository's Security tab on GitHub ("Report a vulnerability"): https://github.com/queroautomacao/tuya-ip-hub/security/advisories/new

If the Security tab shows no "Report a vulnerability" button, private reporting is not enabled on the repository yet: open a public issue that says only that you found a vulnerability and asks for a private channel, with no technical detail.

Include what you can: the version (`versao` in `GET /health`, or the image tag), steps to reproduce, the impact you see, and a fix if you have one. Reports in English or Portuguese are both fine.

### Response times

- Acknowledgement: within 3 business days.
- Triage and severity: within 7 days of the report.
- Fix or mitigation for confirmed issues: targeted within 30 days for high severity, 90 days otherwise. You are kept informed in the advisory.
- Disclosure: coordinated with you, after the fix is released or 90 days after the report, whichever comes first. You are credited in the advisory and in the release notes if you want to be.

Milestone 0 is a skeleton; there is no supported release yet. Fixes land on `main` and, once releases exist, on the latest tag only.

### Scope

In scope, roughly in order of severity:

- Escape from the container to the host, or to other containers on the same host.
- The REST API and the DP-bus WebSocket: authentication bypass, ownership code or password bypass, session or `api_token` leak, missing or bypassable login rate limit, CSRF, DNS rebinding through the Host header, missing security headers.
- The declarative driver engine: a JSON driver must not be able to do more than send bytes to its own device. Code execution, reading of secrets or configuration, requests to arbitrary hosts, or a regex that freezes the daemon are all in scope.
- Credential leak: device credentials, `api_token`, session hashes or the ownership code reaching the panel, the logs, another user or the network.
- Anything that turns the hub into a proxy into the LAN: a route that accepts a hostname or URL where an IP literal is expected, server-side request forgery, open relays.
- Secret files in `/data` created with permissions wider than `0600`.
- The Docker image: running as root, `docker.sock` mounted, a compromised build dependency.

### Out of scope

- The panel is designed for the local network and is served over plain HTTP in the beta. Reports that only point out the absence of TLS, or attacks that require reading LAN traffic, are out of scope for now.
- Exposing port 8080 to the internet against the README's advice.
- Problems that require physical access to the box, or root on the host.
- Vulnerabilities in the controlled devices (TVs, receivers, speakers): report them to the manufacturer.
- Denial of service by traffic volume from a host on the LAN.
- Issues in third-party dependencies without a demonstrated impact on the hub: report them upstream; the dependency is updated here.
- Social engineering of maintainers.

### What the design promises

Section 9 of [CLAUDE.md](CLAUDE.md) lists the security decisions, and each one becomes an attacking test in `core/tests/seguranca` as its milestone lands. In short: an ownership code written at first boot (only whoever has it can set the password); passwords hashed with PBKDF2-HMAC-SHA256; session tokens stored only as hashes, with expiry and revocation; a machine `api_token` never handed to the panel and rotated on password change; login rate limit per real IP plus a global ceiling; the Host header restricted to IP literals, `localhost` and an allowlist (421 otherwise); Origin checked on `/api/*` and `/dpbus` (403); security headers on every response; secret files born `0600`; container as a non-root user without `docker.sock`; every `ip` field validated as an IP literal. One exception to the headers is accepted and recorded: the `400` that the aiohttp HTTP parser emits for a malformed request, before any route runs, carries neither those headers nor the neutral `Server` value; section 9 of [CLAUDE.md](CLAUDE.md) documents it on purpose. A report showing that any of these does not hold is welcome.

## Português

### Reportando uma vulnerabilidade

Não abra issue nem pull request público para um problema de segurança. Reporte em privado pela aba Security do repositório no GitHub ("Report a vulnerability"): https://github.com/queroautomacao/tuya-ip-hub/security/advisories/new

Se a aba Security não mostrar o botão "Report a vulnerability", o relato privado ainda não está habilitado no repositório: abra uma issue pública que diz apenas que você encontrou uma vulnerabilidade e pede um canal privado, sem nenhum detalhe técnico.

Inclua o que puder: a versão (`versao` em `GET /health`, ou a tag da imagem), passos para reproduzir, o impacto que você vê e uma correção se tiver. Relatos em inglês ou português são bem-vindos.

### Prazos de resposta

- Confirmação de recebimento: em até 3 dias úteis.
- Triagem e severidade: em até 7 dias após o relato.
- Correção ou mitigação para problemas confirmados: meta de 30 dias para severidade alta, 90 dias nos demais. Você é mantido informado no advisory.
- Divulgação: coordenada com você, depois da correção publicada ou 90 dias após o relato, o que vier primeiro. Você recebe crédito no advisory e nas notas de release se quiser.

O marco 0 é um esqueleto; ainda não há release com suporte. Correções entram no `main` e, quando houver releases, só na tag mais recente.

### Escopo

Dentro do escopo, mais ou menos em ordem de severidade:

- Escape do container para o host, ou para outros containers no mesmo host.
- A API REST e o WebSocket do DP-bus: burla de autenticação, burla do código de posse ou da senha, vazamento de sessão ou de `api_token`, limite de tentativas de login ausente ou contornável, CSRF, DNS rebinding pelo cabeçalho Host, cabeçalhos de segurança ausentes.
- O motor de driver declarativo: um driver JSON não pode fazer mais do que enviar bytes ao próprio aparelho. Execução de código, leitura de segredos ou configuração, requisições a hosts arbitrários ou uma regex que congela o daemon estão todos no escopo.
- Vazamento de credencial: credenciais de aparelho, `api_token`, hashes de sessão ou o código de posse chegando ao painel, aos logs, a outro usuário ou à rede.
- Qualquer coisa que transforme o hub em proxy para dentro da LAN: rota que aceita nome de host ou URL onde se espera IP literal, server-side request forgery, relays abertos.
- Arquivos de segredo em `/data` criados com permissão mais larga que `0600`.
- A imagem Docker: rodando como root, `docker.sock` montado, dependência de build comprometida.

### Fora do escopo

- O painel é feito para a rede local e é servido por HTTP puro no beta. Relatos que só apontam a ausência de TLS, ou ataques que exigem ler o tráfego da LAN, estão fora do escopo por enquanto.
- Expor a porta 8080 na internet contra o aviso do README.
- Problemas que exigem acesso físico à máquina, ou root no host.
- Vulnerabilidades nos aparelhos controlados (TVs, receivers, caixas): reporte ao fabricante.
- Negação de serviço por volume de tráfego a partir de um host na LAN.
- Problemas em dependências de terceiros sem impacto demonstrado no hub: reporte no projeto de origem; a dependência é atualizada aqui.
- Engenharia social contra os mantenedores.

### O que o projeto promete

A seção 9 do [CLAUDE.md](CLAUDE.md) lista as decisões de segurança, e cada uma vira um teste que ataca em `core/tests/seguranca` conforme o seu marco chega. Em resumo: um código de posse gravado no primeiro boot (só quem o tem define a senha); senhas com PBKDF2-HMAC-SHA256; tokens de sessão guardados só por hash, com validade e revogação; um `api_token` de máquina nunca entregue ao painel e rotacionado na troca de senha; limite de tentativas de login por IP real mais um teto global; cabeçalho Host restrito a IP literal, `localhost` e uma lista permitida (421 nos demais); Origin conferido em `/api/*` e `/dpbus` (403); cabeçalhos de segurança em toda resposta; arquivos de segredo nascem `0600`; container como usuário não-root sem `docker.sock`; todo campo `ip` validado como IP literal. Uma exceção aos cabeçalhos é aceita e registrada: o `400` que o parser HTTP do aiohttp emite para requisição malformada, antes de qualquer rota, não leva esses cabeçalhos nem o valor neutro de `Server`; a seção 9 do [CLAUDE.md](CLAUDE.md) o registra de propósito. Um relato mostrando que qualquer um desses itens não se sustenta é bem-vindo.
