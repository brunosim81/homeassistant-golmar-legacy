# Golmar G2Call legacy para Home Assistant

Integração local experimental, versão **0.4.0**, para **Golmar ART 7W G2+ V03, firmware 1.03, G2Call legacy**. Não é G2Call+ nem uma integração oficial Golmar, Apple ou Home Assistant.

## Validado num monitor real

- Autenticação local por IP e contra-senha, TCP 5801, OEM G0053. O UID não é necessário no login LAN.
- Vídeo H.264 na app Casa, com imagem em movimento em cerca de 5 a 6 segundos no teste observado. Resolução do equipamento testado: 352 × 288.
- Toque físico da campainha: notificação da Casa recebida em menos de 5 segundos, com acesso ao vídeo. Depois de fechar a imagem, o menu do monitor continuou normal.
- Uma abertura física confirmada pelo proprietário após uma única tentativa autorizada. Isto não comprova fiabilidade em todos os cenários ou equipamentos.
- Botão de simulação de campainha, identificado como teste, sem contactar o monitor.

Validação limitada a um equipamento. Tempos são observações desse teste, não garantias. A abertura tinha apresentado falhas intermitentes em versões anteriores.

## O que mudou

A receção de campainha usa uma ligação autenticada que recebe eventos espontâneos `0x505`, canal 0, alarme 7. **Não envia GUARD/UNGUARD**: a subscrição antiga colocou o monitor em estado de chamada e foi removida. Na ligação de eventos só são permitidos HELLO, LOGIN e heartbeat. Ao parar, a ligação é fechada.

O vídeo partilha uma única sessão do monitor entre até quatro leitores, com filas limitadas. A sessão tem um limite de 45 segundos e fecha após cerca de 2 segundos sem leitores. As miniaturas usam a imagem em cache e não iniciam vídeo. O fim de uma sessão pode exigir reabrir a imagem; não é um serviço de gravação contínua.

Durante um pedido explícito de abertura, a integração suspende as suas sessões de vídeo e eventos e retoma a receção no fim. Não repete automaticamente comandos de abertura.

## Instalação e atualização manual

1. Fazer uma cópia de segurança do Home Assistant.
2. Copiar apenas `custom_components/golmar_legacy` deste repositório para `config/custom_components/golmar_legacy`.
3. Reiniciar o HA. Numa instalação nova, adicionar **Golmar G2Call legacy** em Definições > Dispositivos e serviços e indicar o IP atual e a contra-senha conhecida.
4. Começar por **Testar ligação**, que autentica sem abrir a porta. A integração também mantém a ligação de receção de eventos enquanto está carregada.
5. O botão **Abrir porta** fica desativado por defeito no registo de entidades. Ativá-lo e usá-lo pode atuar fisicamente a fechadura.

Preservar outras integrações, incluindo `golmar_quvii` ou `xmeye`. Não é necessário remover nem voltar a emparelhar pontes HomeKit para atualizar esta integração. O adaptador de vídeo escuta apenas em loopback, com caminho aleatório.

## Apple Casa

Criar uma entrada HomeKit Bridge separada em modo **accessory**, selecionando apenas a câmara Golmar. Emparelhar na Casa. No HA 2026.10.4, o evento `doorbell` do mesmo dispositivo é associado à câmara. Ativar as notificações da campainha nas definições dessa câmara na Casa.

Para o botão de abertura, uma segunda entrada HomeKit em modo **accessory**, incluindo apenas o botão **Abrir porta**, permite emparelhá-lo e escolher o nome e a divisão independentemente. Colocar câmara e abertura na mesma divisão. Se o botão já estiver numa ponte geral, excluí-lo apenas dessa ponte para evitar duplicados, preservando os restantes filtros e acessórios. Não publicar nem reutilizar códigos de emparelhamento de outra instalação.

O botão aparece como controlo de ativação. Não é um sensor da posição da porta. Não o premir para testar a ligação ou a edição do nome.

No monitor testado, o desvio de chamada estava inicialmente em 15 segundos e o proprietário colocou-o em 0. Consultar esta opção se houver atraso, usando as definições oficiais do monitor. O teste final da campainha foi feito com 0 segundos. A app G2Call não recebia chamadas nesse equipamento, mas a receção local na Casa funcionou.

## Limitações

- Sem leitura da posição física da porta ou da fechadura.
- Sem áudio bidirecional. O áudio da câmara também não foi validado.
- A Casa apresenta notificações de campainha com vídeo, não chamadas de ecrã inteiro CallKit/FaceTime.
- Outros modelos, canais, tipos de evento e firmwares não foram validados. Eventos desconhecidos são ignorados.
- A ligação simultânea de outras apps pode afetar o monitor. Se o menu ficar bloqueado, fechar o vídeo e desativar apenas esta integração. Não voltar a ativar GUARD/UNGUARD.
- Se uma abertura falhar depois do envio do comando, confirmar fisicamente o resultado antes de repetir.
- Não incluída no catálogo HACS. Instalação manual.

## Referências de contra-senha

`12345678` e `1234` são referências de valores pré-definidos indicadas durante a preparação do projeto. Não foi confirmado que sejam senhas de fábrica universais. Usar apenas a contra-senha conhecida e autorizada do próprio equipamento, sem listas de tentativas ou reposição do monitor.

A contra-senha G2Call e o PIN de abertura são campos distintos. A integração não inclui credenciais reais nem preenche uma contra-senha por defeito. O HA guarda a configuração através dos seus mecanismos normais; não partilhar `.storage`.

## Testes e privacidade

54 testes automatizados com HA 2026.10.4 e Python 3.14, usando servidores locais simulados. Não contactam um monitor real. Cobrem autenticação, framing TCP, rejeição de comandos na ligação de eventos, deduplicação, reconexão, encerramento, concorrência de vídeo e abertura explícita no simulador.

Os endereços e senhas dos testes são fictícios. Não publicar UID, senhas, tokens, códigos ou QR HomeKit, capturas de rede, imagens privadas, backups do HA ou do telefone. Não expor portas do monitor à Internet. Este repositório contém apenas código, testes e documentação revistos.

## Reverter

Desativar apenas a integração Golmar no HA para fechar as sessões. Para remover, descarregar a entrada e remover apenas `custom_components/golmar_legacy`. Preservar outras integrações e pontes. Não instalar versões antigas que usem GUARD/UNGUARD neste modelo.

## Origem e licença

MIT. Formato de mensagens adaptado de [jdntortosa/fermax-wayfi-ha](https://github.com/jdntortosa/fermax-wayfi-ha), commit `5f4a1dfeb44d6f925b29834f7927e11f3c546312`. Avisos originais preservados. Autenticação G0053 e transformação da contra-senha estudadas em ambiente próprio autorizado. Não são distribuídos APK, IPA, SDK ou binários de fabricantes.
