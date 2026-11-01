# Histórico

## 0.4.1, 2026-11-01

- Ícone local de videoporteiro, branco sobre azul, em 256 e 512 pixels. Sem alterações ao protocolo ou às entidades.

## 0.4.0, 2026-11-01

### Adicionado
- Receção local de campainha real sem subscrição GUARD/UNGUARD.
- Notificação da Casa com vídeo validada num monitor, recebida em menos de 5 segundos.

### Corrigido
- Removida a subscrição que colocava o monitor em estado de chamada.
- Vídeo com uma origem partilhada, leitores limitados e encerramento independente do cliente.
- Sessões da integração suspensas durante a abertura explícita, sem repetição automática.

### Documentado
- Validação de uma abertura física e limites dessa confirmação.
- Emparelhamento independente do botão de abertura na Casa.
- 54 testes com simuladores; sem áudio bidirecional ou estado físico de fechadura.

## 0.2.0

- Autenticação local, vídeo H.264 e campainha simulada. Sem deteção real validada nessa versão.
