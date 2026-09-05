# Device matrix (Matriz de aparelhos)

[English](#english) | [Português](#português)

The table itself is at the end of this file, shared by both languages, so that a device row is added once.

A tabela em si está no fim deste arquivo, compartilhada pelas duas línguas, para que a linha de um aparelho seja acrescentada uma vez só.

## English

### Three honest states

| State | Meaning |
|---|---|
| `verificado` | Verified on hardware: someone ran the hub against the real device, exercised the capabilities the driver declares, and signs the row with their GitHub username and the date. |
| `simulado` | Tested against the simulated device: the driver's test suite passes against `simulado.py`, fed with recordings of real traffic when they exist. Nobody has signed a run on the real device yet. |
| `declarado` | Declared: the manifest claims support based on protocol documentation, and no test against a recording or a real device exists yet. |

A state moves up only through a pull request that says what was run. It never moves up because "it should work". Hardware validation is a row here, never a merge gate: the CI tests every driver against the simulated device.

### Adding a row

1. Open a pull request that edits the table at the end of this file, one row per device model and driver. Keep the rows sorted by brand, then model.
2. Fill the columns:
   - brand;
   - model, with the firmware version when you know it;
   - driver: the manifest `tipo`, for example `linkplay`;
   - state: one of the three above;
   - who, when: your GitHub username and the date as `YYYY-MM-DD` for `verificado`; the pull request number for `simulado`; blank for `declarado`;
   - notes: what was not tested, quirks, pairing requirements, network requirements.
3. Never attach manufacturer manuals or PDFs; link to the public page instead.
4. No real network addresses in the notes; use `192.0.2.x` when an example is needed.
5. A row in state `verificado` must come from the person who ran the test. Signing for someone else is not a verification.

## Português

### Três estados honestos

| Estado | Significado |
|---|---|
| `verificado` | Verificado em hardware: alguém rodou o hub contra o aparelho real, exercitou as capacidades que o driver declara e assina a linha com o seu usuário do GitHub e a data. |
| `simulado` | Testado contra o aparelho simulado: a suíte de testes do driver passa contra o `simulado.py`, alimentado por gravações de tráfego real quando existem. Ninguém assinou ainda uma rodada no aparelho real. |
| `declarado` | Declarado: o manifesto reivindica suporte com base em documentação de protocolo, e ainda não existe teste contra gravação nem contra aparelho real. |

Um estado só sobe por um pull request que diz o que foi rodado. Nunca sobe porque "deveria funcionar". Validação em hardware é uma linha aqui, nunca portão de integração: o CI testa todo driver contra o aparelho simulado.

### Acrescentando uma linha

1. Abra um pull request que edita a tabela no fim deste arquivo, uma linha por modelo de aparelho e driver. Mantenha as linhas ordenadas por marca, depois modelo.
2. Preencha as colunas:
   - marca;
   - modelo, com a versão de firmware quando souber;
   - driver: o `tipo` do manifesto, por exemplo `linkplay`;
   - estado: um dos três acima;
   - quem, quando: o seu usuário do GitHub e a data como `AAAA-MM-DD` para `verificado`; o número do pull request para `simulado`; em branco para `declarado`;
   - observações: o que não foi testado, particularidades, exigências de pareamento, exigências de rede.
3. Nunca anexe manuais nem PDFs de fabricante; coloque o link da página pública.
4. Sem endereço de rede real nas observações; use `192.0.2.x` quando precisar de exemplo.
5. Uma linha no estado `verificado` precisa vir de quem rodou o teste. Assinar por outra pessoa não é verificação.

## Matrix (Matriz)

| Brand (Marca) | Model (Modelo) | Driver | State (Estado) | Who, when (Quem, quando) | Notes (Observações) |
|---|---|---|---|---|---|
| LinkPlay | A31 (uyesee-i50), firmware 4.6 | `multiroom_linkplay` | `verificado` | queroautomacao, 2026-09-04 | Verified on two speakers, with the exact strings the driver puts on the wire: identity by `uuid` from `getStatusEx`, the `getPlayerStatus` poll (metadata is hexadecimal, and a streaming service answers a mode the input table does not name), `setPlayerCmd:vol`, `setPlayerCmd:resume` and `setPlayerCmd:pause` (a paused speaker went to playing and the position advanced). NOT exercised: mute, input, grouping, next and previous track, stop, radios and presets (`atalho`), whose driver paths were not run on the device; since 2026-09-05 they all go over the HTTP API of the module (`setPlayerCmd:mute`, `switchmode`, `next`, `prev`, `stop`, `MCUKeyShortClick`, `multiroom:SlaveKickout`), the iEAST control port on TCP 8899 being retired. A speaker idle after leaving a group answers `mode 99` with `group 0`, so the driver reads the slave state from the `group` field of `getStatusEx`. The speaker answers `OK` to any command, including one that does not exist, so its answer is never a confirmation. (Verificado em duas caixas, com as strings exatas que o driver põe no fio: identidade pelo `uuid` do `getStatusEx`, o poll `getPlayerStatus` (metadado em hexadecimal, e um serviço de streaming responde um modo que a tabela de entradas não nomeia), `setPlayerCmd:vol`, `setPlayerCmd:resume` e `setPlayerCmd:pause` (uma caixa pausada foi para tocando e a posição andou). NÃO exercitados: mudo, entrada, agrupamento, próxima e anterior faixa, parar, rádios e presets (`atalho`), cujos caminhos do driver não foram rodados no aparelho; desde 2026-09-05 todos vão pela API HTTP do módulo (`setPlayerCmd:mute`, `switchmode`, `next`, `prev`, `stop`, `MCUKeyShortClick`, `multiroom:SlaveKickout`), com a porta de controle iEAST na TCP 8899 aposentada. Uma caixa parada depois de sair de um grupo responde `mode 99` com `group 0`, então o driver lê o estado de escrava pelo campo `group` do `getStatusEx`. A caixa responde `OK` a qualquer comando, inclusive a um que não existe, então a resposta dela nunca é confirmação.) |
| Denon / Marantz | AVR with the HTTP interface (AVR com a interface HTTP) | `receiver_denon` | `simulado` | | Written from the IP command chart of Denon and from the `denonavr` library the Home Assistant integration uses, and tested against a simulated receiver. HTTP only, never telnet: section 14 measured that a Denon accepts ONE telnet connection at a time and fights with any other controller that wants it. A command is a GET on `/goform/formiPhoneAppDirect.xml?<command>` (PWON, MV50, MUON, SIBD, MSMOVIE) and the state is one GET on `/goform/formMainZone_MainZoneXmlStatusLite.xml`. The port is 8080 on the AVR-X of 2016 and later and 80 on the older ones, so the driver tries both and keeps the one that answered. NOT exercised on a device: everything. (Escrito da tabela de comandos IP da Denon e da biblioteca `denonavr` que a integração do Home Assistant usa, e testado contra um receiver simulado. Só HTTP, nunca telnet: a seção 14 mediu que um Denon aceita UMA conexão telnet por vez e briga com qualquer outro controlador que a queira. Um comando é um GET em `/goform/formiPhoneAppDirect.xml?<comando>` (PWON, MV50, MUON, SIBD, MSMOVIE) e o estado é um GET em `/goform/formMainZone_MainZoneXmlStatusLite.xml`. A porta é 8080 no AVR-X de 2016 em diante e 80 nos mais antigos, então o driver tenta as duas e guarda a que respondeu. NÃO exercitado em aparelho: tudo.) |
| LG | Split Wi-Fi ThinQ (ar condicionado) | `ar_lg_thinq` | `simulado` | | The Wi-Fi split of LG has NO local API: the maker publishes only the ThinQ Connect cloud, which is the door the `lg_thinq` integration of Home Assistant goes through, so this is the first cloud driver of the hub (section 1). Written from the documentation of LG and from that integration, and tested against a simulated cloud: nothing was run against a real LG account, because a suite that spent the account of a customer is not a suite. NOT exercised on a device: everything. What the driver does: reads `devices/{id}/state`, learns the words of that model from `devices/{id}/profile`, and writes power, setpoint, mode and fan speed on `devices/{id}/control`. (O split Wi-Fi da LG NÃO tem API local: o fabricante publica só a nuvem ThinQ Connect, que é a porta por onde passa a integração `lg_thinq` do Home Assistant, então este é o primeiro driver de nuvem do hub (seção 1). Escrito da documentação da LG e daquela integração, e testado contra uma nuvem simulada: nada foi rodado contra conta LG de verdade, porque uma suíte que gastasse a conta de um cliente não é suíte. NÃO exercitado em aparelho: tudo. O que o driver faz: lê o `devices/{id}/state`, aprende as palavras daquele modelo no `devices/{id}/profile`, e escreve energia, setpoint, modo e vento no `devices/{id}/control`.) |
| PJLink | Class 1, generic (genérico) | `projetor_pjlink` | `simulado` | | Simulated device only (Só aparelho simulado.) |
