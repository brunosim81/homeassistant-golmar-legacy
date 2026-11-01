# Golmar G2Call legacy para Home Assistant

Integração experimental para Golmar ART 7W G2+ V03, firmware 1.03, com G2Call legacy. Não é uma integração G2Call+.

## Estado comprovado

- Autenticação local na porta TCP 5801, OEM G0053, com IP e contra-senha do monitor.
- Imagem e vídeo H.264, validados num monitor com resolução 352 × 288.
- Vídeo na app Casa através do HomeKit Bridge do Home Assistant.
- Evento de campainha simulado: aviso recebido num iPhone, com acesso ao vídeo.

Validação feita num único equipamento. Isto não garante compatibilidade com todos os modelos ou firmwares.

## Limites importantes

- A deteção de campainha real ainda não está implementada. Não há avisos automáticos quando alguém toca fisicamente.
- O botão Abrir porta é experimental e fica desativado por defeito. A abertura física não foi validada pelo projeto. Ativá-lo e usá-lo pode atuar a fechadura; não é um teste de ligação.
- Não existe leitura da posição física da fechadura.
- Não há áudio bidirecional nem áudio validado na câmara publicada.
- A Casa apresenta avisos de campainha, não chamadas CallKit/FaceTime.
- Não é uma integração oficial Golmar, Apple ou Home Assistant, nem uma integração incluída no catálogo HACS.

## Instalação manual

1. Fazer uma cópia de segurança do Home Assistant.
2. Copiar apenas `custom_components/golmar_legacy` para `config/custom_components/golmar_legacy`.
3. Reiniciar o Home Assistant e adicionar Golmar G2Call legacy em Definições > Dispositivos e serviços.
4. Indicar o IP atual e a contra-senha de ligação conhecida. O UID não é usado no login local. Não tentar listas de senhas nem repor o monitor.
5. Começar pelo botão Testar ligação. Este faz apenas autenticação e não abre a porta.

Preservar integrações existentes, incluindo golmar_quvii ou xmeye. A integração cria um adaptador de vídeo apenas em loopback, com caminho aleatório. O vídeo é pedido quando um cliente o solicita.

## Apple Casa

Criar uma entrada HomeKit Bridge separada em modo accessory, incluindo apenas a entidade de câmara do Golmar. Emparelhar essa câmara na Casa. No Home Assistant 2026.10.4, a entidade de evento doorbell do mesmo dispositivo é associada à câmara.

O botão Simular campainha (teste) emite um evento local marcado como simulado e pode produzir uma notificação real no iPhone. Não envia comandos ao monitor. A receção depende das definições de notificações e da configuração HomeKit de cada casa.

## Investigação pendente

O protocolo contém StartGetAlarm/StopGetAlarm (mensagens GUARD/UNGUARD). Num ensaio limitado, o monitor aceitou ambas, mas não houve toque físico durante a captura. A correspondência entre os eventos locais e o toque real permanece por validar. Não ativamos subscrição automática nem assumimos ausência de efeitos persistentes no equipamento.

Os valores de aplicação ring=7 e motion=2 são pistas de investigação, não uma prova dos valores recebidos localmente.

## Testes e contributos

26 testes automatizados passaram na preparação original com bibliotecas de Home Assistant 2026.10.4/Python 3.14. Esses testes usam simuladores e não certificam ações físicas. Os testes estão incluídos; os endereços privados dos exemplos são fictícios.

Contributos úteis: modelo e firmware, versão HA, confirmação de vídeo e capturas mínimas de um toque físico autorizado. Não publicar senhas, UID, tokens, QR HomeKit, cópias `.storage`, backups de telefone ou imagens de pessoas. Não expor portas do monitor à Internet.

## Origem e licença

MIT. Formato de mensagens adaptado de [jdntortosa/fermax-wayfi-ha](https://github.com/jdntortosa/fermax-wayfi-ha), commit `5f4a1dfeb44d6f925b29834f7927e11f3c546312`. Os avisos de licença originais estão preservados. Autenticação G0053 e transformação da contra-senha estudadas em ambiente próprio autorizado. Não são distribuídos APK, IPA, SDK ou binários de fabricantes.
