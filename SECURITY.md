# Security Policy

[English](#english) | [Português](#português)

## English

### Reporting

Do not open a public issue or pull request for a security problem. Report it privately through the Security tab: https://github.com/queroautomacao/tuya-ip-hub/security/advisories/new. If that button is missing, open an issue saying only that you found a vulnerability and need a private channel. Include the version (`versao` in `GET /health`), steps to reproduce and the impact, in English or Portuguese.

Acknowledgement within 3 business days, triage within 7 days, fix targeted within 30 days for high severity and 90 days otherwise, disclosure coordinated with you. Fixes land on `main` and, once releases exist, on the latest tag.

### Scope

In scope: container escape; authentication or password bypass on the REST API or the DP-bus; anything that reaches the panel through the remote access without the code of the window, without the second factor, or outside the window of 24 hours; anything that makes the relay reach a hub that did not dial it; leak of sessions, `api_token` or device credentials to the panel, the logs or the network; missing login rate limit, CSRF, DNS rebinding through the Host header, missing security headers; anything that turns the hub into a proxy into the LAN; secret files wider than `0600`; the image running as root.

Out of scope: the absence of TLS on the local network (the remote access is HTTPS up to the relay); claiming a hub that has no password yet (the first access is public by design); port 8080 exposed to the internet against the README; physical or root access to the host; vulnerabilities in the controlled devices; denial of service by traffic volume from the LAN; third-party dependencies without a demonstrated impact on the hub.

### What the design promises

Passwords hashed with PBKDF2-HMAC-SHA256; session tokens stored as hashes, with expiry and revocation; a machine `api_token` never handed to the panel and rotated on password change; login rate limit per IP plus a global ceiling; Host restricted to IP literals, `localhost` and an allowlist; Origin checked on `/api/*` and `/dpbus`; security headers on every response, except the `400` the HTTP parser emits before any route runs; secret files born `0600`, and the secrets inside them (licence keys, equipment credentials, the authenticator secret, the DP-bus credential) encrypted at rest with a key derived from the serial of the board, so the card alone opens none of them; backups encrypted with a password of their own, restored only with it; a non-root container without `docker.sock`; every `ip` field validated as an IP literal; cloud drivers on a fixed host over HTTPS, with no redirects and no credential in the log. Remote access is closed by default and opens for at most 24 hours at a time, on a window kept as an absolute instant, so it closes even if the daemon was down when it expired; while it is closed the hostname of the relay is not in the Host allowlist at all. Inside it, the hub holds one outbound socket to the relay and answers only what arrives over it, with a session that showed the code of the window first, then the password and a code of an authenticator app; the window code is drawn when the window is armed, published to the app of the customer and dead when it closes, and a session opened through the relay dies with the window it was opened in; the claim of ownership and the DP-bus never answer through the relay. The relay decides nothing about a customer: the address it hands out is the secret, and it reaches only the socket that took it; what it checks is that the installation is registered by the maker, by asking the maker's own registry about the home of the app the hub says it belongs to. There is no password recovery over the network: `python -m iphub.esquecer` inside the container clears the password from the host.

## Português

### Relato

Não abra issue nem pull request público para um problema de segurança. Reporte em privado pela aba Security: https://github.com/queroautomacao/tuya-ip-hub/security/advisories/new. Se esse botão não existir, abra uma issue dizendo apenas que encontrou uma vulnerabilidade e precisa de um canal privado. Inclua a versão (`versao` em `GET /health`), os passos para reproduzir e o impacto, em inglês ou português.

Confirmação em até 3 dias úteis, triagem em até 7 dias, correção com meta de 30 dias para severidade alta e 90 dias nos demais, divulgação coordenada com você. Correções entram no `main` e, quando houver releases, na tag mais recente.

### Escopo

No escopo: escape do container; burla de autenticação ou de senha na API REST ou no DP-bus; qualquer coisa que alcance o painel pelo acesso remoto sem o código da janela, sem o segundo fator, ou fora da janela de 24 horas; qualquer coisa que faça o relay alcançar um hub que não discou para ele; vazamento de sessões, `api_token` ou credenciais de aparelho para o painel, os logs ou a rede; limite de login ausente, CSRF, DNS rebinding pelo cabeçalho Host, cabeçalhos de segurança ausentes; qualquer coisa que transforme o hub em proxy para dentro da LAN; arquivos de segredo mais largos que `0600`; a imagem rodando como root.

Fora do escopo: a ausência de TLS na rede local (o acesso remoto é HTTPS até o relay); tomar posse de um hub que ainda não tem senha (o primeiro acesso é público por desenho); a porta 8080 exposta na internet contra o README; acesso físico ou root ao host; vulnerabilidades nos aparelhos controlados; negação de serviço por volume de tráfego da LAN; dependências de terceiros sem impacto demonstrado no hub.

### O que o projeto promete

Senhas com PBKDF2-HMAC-SHA256; tokens de sessão guardados por hash, com validade e revogação; um `api_token` de máquina nunca entregue ao painel e rotacionado na troca de senha; limite de login por IP mais um teto global; Host restrito a IP literal, `localhost` e uma lista permitida; Origin conferido em `/api/*` e `/dpbus`; cabeçalhos de segurança em toda resposta, exceto o `400` que o parser HTTP emite antes de qualquer rota; arquivos de segredo nascem `0600`, e os segredos dentro deles (chaves de licença, credenciais de equipamento, segredo do autenticador, credencial do DP-bus) ficam cifrados em repouso com uma chave derivada do número de série da placa, então o cartão sozinho não abre nenhum deles; backups cifrados com senha própria, restaurados só com ela; container não-root sem `docker.sock`; todo campo `ip` validado como IP literal; drivers de nuvem em host fixo por HTTPS, sem redirecionamento e sem credencial no log. O acesso remoto nasce fechado e abre por no máximo 24 horas de cada vez, numa janela guardada como instante absoluto, então ela fecha mesmo que o daemon estivesse fora do ar quando venceu; enquanto está fechada, o hostname do relay nem sequer está na lista de hosts permitidos. Dentro dela, o hub mantém um socket de saída para o relay e só atende o que chega por ele, com uma sessão que mostrou primeiro o código da janela, depois a senha e um código de aplicativo autenticador; o código da janela é sorteado ao armar, publicado no app do cliente e morre quando ela fecha, e uma sessão aberta pelo relay morre com a janela em que foi aberta; a tomada de posse e o DP-bus nunca atendem pelo relay. O relay não decide nada sobre cliente: o endereço que ele entrega é o segredo, e ele alcança só o socket que o tomou; o que ele confere é que a instalação foi cadastrada pelo fabricante, perguntando ao cadastro do próprio fabricante sobre o Home do aplicativo que o hub diz ser o dele. Não há recuperação de senha pela rede: `python -m iphub.esquecer` dentro do container apaga a senha a partir do host.
