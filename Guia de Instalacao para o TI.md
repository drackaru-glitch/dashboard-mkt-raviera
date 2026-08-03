# Guia de Instalação — Raviera Cockpit (para o TI)

> Material pra equipe de TI hospedar o Raviera Cockpit num subdomínio da empresa. Os nomes técnicos (pasta do projeto, repositório, serviço systemd, etc.) continuam usando "raviera"/"dashboard-raviera" internamente — só o nome exibido pro usuário mudou.
> Dúvidas técnicas sobre este guia: fale com Felipe (responsável pelo projeto).
>
> **Versão deste guia: 0.44** (o número da versão instalada aparece no rodapé do próprio sistema, e o histórico de mudanças de cada versão fica em `CHANGELOG.md` na raiz do repositório).

---

## 0. Antes de começar: conhecido/pendente nesta versão

- **Google Ads não está sincronizando** — o token de autorização expirou e precisa ser gerado de novo (não é um problema da instalação; já estava assim antes de subir pro servidor). O dashboard de Google Ads carrega normalmente, só não traz dado novo até isso ser resolvido.
- **SAC (Instagram/Facebook)** ainda em modo demonstração — depende de uma permissão extra do Meta que ainda não foi liberada.
- Isso não impede a instalação nem afeta Meta Ads, Instagram orgânico, Funil/Informações Gerais ou Notícias do Setor, que já funcionam com dados reais.

---

## 1. O que é esta aplicação

- **Backend**: API em Python (FastAPI), banco de dados SQLite (arquivo único), sem dependências externas de infraestrutura (não usa Redis, filas, etc.).
- **Frontend**: um único arquivo HTML (`frontend/index.html`) com React carregado via CDN — não tem build (webpack/vite/npm), o próprio backend serve esse arquivo direto.
- **Porta interna**: a aplicação roda na porta `8000` (via Uvicorn). Em produção, essa porta **não deve ficar exposta direto pra internet** — um proxy reverso (Nginx) na frente cuida do HTTPS e repassa pra ela.
- **Importante — rodar com um único processo**: a aplicação tem um agendador interno (sincroniza sozinha às 06h todo dia). Se o Uvicorn for configurado com `--workers 2` (ou mais) achando que isso melhora performance, o agendador roda em dobro/triplo e sincroniza a mesma coisa várias vezes ao mesmo tempo. Os arquivos prontos deste guia (`deploy/dashboard-raviera.service`, `deploy/Dockerfile`) já rodam com um processo só — não adicione `--workers` a eles.
- Já existe um repositório Git com todo o código: `https://github.com/drackaru-glitch/dashboard-mkt-raviera` (pode cloná-lo direto no servidor).

---

## 2. Requisitos do servidor

| Item | Requisito |
|---|---|
| Sistema operacional | Linux (Ubuntu/Debian recomendado) — evitar Windows, tivemos instabilidade rodando lá durante o desenvolvimento |
| Python | 3.12 ou superior |
| Memória/CPU | Bem leve — 1 vCPU / 1 GB RAM já é suficiente |
| Acesso | SSH com um usuário que possa instalar pacotes (sudo) |
| Rede | Porta 443 (HTTPS) liberada de entrada; a porta 8000 fica só interna (`127.0.0.1`) |
| Subdomínio | Ex: `dashboard.gruporaviera.com.br`, com DNS (registro A) apontando pro IP do servidor |
| Certificado HTTPS | Let's Encrypt via Certbot (grátis) resolve bem, a não ser que a empresa já tenha um certificado wildcard próprio |

---

## 3. Quais arquivos sobem, e como

### Opção recomendada: `git clone` direto no servidor

Como o código já está no GitHub, o jeito mais simples é o próprio servidor puxar o repositório:

```bash
cd /opt
git clone https://github.com/drackaru-glitch/dashboard-mkt-raviera.git
```

Isso já traz a estrutura certa. **Só que dois arquivos importantes não vêm pelo Git (de propósito, por segurança)** e precisam ser transferidos separadamente, por um canal seguro (não por e-mail/chat aberto):

| Arquivo | O que é | Como obter |
|---|---|---|
| `backend/.env` | Credenciais reais (tokens do Meta, Google Ads, chave do webhook do Followize, segredo do login) | Peça pro Felipe te enviar o conteúdo desse arquivo diretamente (ele já existe configurado) |
| `backend/raviera.db` | Banco de dados com as marcas, contas, usuários e histórico já cadastrados | Peça pro Felipe te enviar esse arquivo também — **evita ter que recadastrar as 24 marcas do zero** |

Se preferir começar do zero (banco vazio, sem as marcas já cadastradas), pule o `raviera.db` — a aplicação cria um banco novo sozinha na primeira vez que rodar, e um usuário admin inicial é criado pelo script `seed.py` (passo 4.4).

### Se não for usar Git (upload manual via SFTP/zip)

Suba exatamente esta estrutura:

```
dashboard-mkt-raviera/
├── backend/
│   ├── app/                  ← pasta inteira (código-fonte)
│   ├── requirements.txt
│   ├── seed.py
│   ├── .env                  ← enviado separadamente (não fica no zip do código)
│   └── raviera.db             ← enviado separadamente, se for migrar os dados existentes
└── frontend/
    └── index.html
```

**NÃO suba**: `backend/.venv/` (ambiente virtual — é recriado no servidor), `backend/__pycache__/`, `backend/*.log`, nem a pasta `.git/` (desnecessária fora do fluxo de clone).

---

## 4. Passo a passo (Ubuntu/Debian)

### 4.1 Instalar dependências do sistema

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip nginx certbot python3-certbot-nginx git
```

### 4.2 Obter o código

```bash
cd /opt
sudo git clone https://github.com/drackaru-glitch/dashboard-mkt-raviera.git
cd dashboard-mkt-raviera
```

### 4.3 Criar ambiente virtual e instalar dependências Python

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

### 4.4 Configurar as credenciais (`.env`) e o banco de dados

- Coloque o arquivo `.env` (recebido do Felipe separadamente) dentro de `backend/.env`.
- Se recebeu o `raviera.db` já pronto, coloque em `backend/raviera.db` — os dados existentes (marcas, usuários, histórico) são carregados automaticamente.
- Se for começar do zero (banco vazio), rode:
  ```bash
  .venv/bin/python seed.py
  ```
  Isso cria o primeiro usuário administrador, usando `ADMIN_EMAIL`/`ADMIN_PASSWORD` definidos no `.env`.

### 4.5 Testar rodando manualmente (antes de virar serviço)

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Em outro terminal, confirme que responde:

```bash
curl -i http://127.0.0.1:8000/
```

Deve retornar `200 OK` com o HTML do painel. Depois, `Ctrl+C` pra parar — o próximo passo faz isso rodar como serviço permanente.

### 4.6 Configurar como serviço permanente (systemd)

Use o arquivo pronto em `deploy/dashboard-raviera.service` deste repositório (ajuste os caminhos se o projeto não estiver em `/opt/dashboard-mkt-raviera`, e o `User=` pro usuário do sistema que vai rodar):

```bash
sudo cp deploy/dashboard-raviera.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable dashboard-raviera
sudo systemctl start dashboard-raviera
sudo systemctl status dashboard-raviera
```

A partir daqui, a aplicação reinicia sozinha se cair ou se o servidor reiniciar.

### 4.7 Configurar o subdomínio (DNS)

No provedor de DNS da empresa, crie um registro **A** (ou CNAME) para o subdomínio escolhido (ex: `dashboard.gruporaviera.com.br`) apontando pro IP público do servidor.

### 4.8 Configurar o proxy reverso (Nginx) + HTTPS

Use o arquivo pronto em `deploy/nginx-dashboard-raviera.conf` (troque o nome do subdomínio dentro dele antes):

```bash
sudo cp deploy/nginx-dashboard-raviera.conf /etc/nginx/sites-available/dashboard-raviera
sudo ln -s /etc/nginx/sites-available/dashboard-raviera /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# gera o certificado HTTPS automaticamente e já ajusta o nginx:
sudo certbot --nginx -d dashboard.gruporaviera.com.br
```

### 4.9 Verificação final

- Acesse `https://dashboard.gruporaviera.com.br` no navegador — deve aparecer a tela de login do painel.
- Confirme que `http://` (sem "s") redireciona automaticamente pra `https://` (o Certbot configura isso).
- Teste o login com o usuário admin existente (ou o criado pelo `seed.py`).

---

## 5. Alternativa: Docker

Se a equipe de TI preferir containers em vez de systemd direto no servidor, tem um `Dockerfile` pronto em `deploy/Dockerfile`. Repare que ainda é necessário o Nginx (ou outro proxy) na frente pra cuidar do HTTPS — o container só expõe a aplicação na porta 8000.

```bash
docker build -f deploy/Dockerfile -t dashboard-raviera .
docker run -d --name dashboard-raviera -p 8000:8000 \
  --env-file backend/.env \
  -v dashboard_raviera_db:/app/backend \
  --restart always \
  dashboard-raviera
```

---

## 6. Depois de publicado: registrar os webhooks do Followize

Assim que o domínio estiver no ar com HTTPS funcionando, avise o Felipe — ele vai te passar 3 URLs (com um token de segurança já embutido) pra cadastrar no painel do Followize/Dealer Space, em Configurações → Integrações → Webhook. Formato geral:

```
https://dashboard.gruporaviera.com.br/webhooks/followize/lead-criado?token=...
https://dashboard.gruporaviera.com.br/webhooks/followize/proposta-submetida?token=...
https://dashboard.gruporaviera.com.br/webhooks/followize/finalizacao?token=...
```

---

## 7. Manutenção básica

| Ação | Comando |
|---|---|
| Ver logs em tempo real | `journalctl -u dashboard-raviera -f` |
| Reiniciar a aplicação | `sudo systemctl restart dashboard-raviera` |
| Atualizar o código (nova versão) | `cd /opt/dashboard-mkt-raviera && sudo git pull && sudo systemctl restart dashboard-raviera` |
| Ver qual versão está rodando | Olhar o rodapé do sistema no navegador (ex.: "v0.44"), ou `cat /opt/dashboard-mkt-raviera/VERSION` no servidor |
| Backup do banco | Copiar o arquivo `backend/raviera.db` periodicamente (é só um arquivo, backup simples) |

### Como funcionam as atualizações (versões)

Cada entrega relevante do projeto vira uma versão nova (0.44, 0.45, 0.46...), registrada em `CHANGELOG.md` na raiz do repositório e commitada/enviada ao GitHub. Pra atualizar o servidor pra última versão publicada, o comando é sempre o mesmo:

```bash
cd /opt/dashboard-mkt-raviera
sudo git pull
sudo systemctl restart dashboard-raviera
```

Duas coisas pra ficar de olho antes de rodar isso:
- **Confira o `CHANGELOG.md`** da nova versão — raramente, uma mudança de banco de dados exige um comando extra (o próprio changelog avisa quando isso acontece; se não avisar, é só `git pull` + reiniciar mesmo).
- O `backend/.env` e `backend/raviera.db` **não fazem parte do `git pull`** (ficam de fora do repositório de propósito) — uma atualização normal nunca mexe neles.

---

## 8. Checklist resumido pra copiar/colar

- [ ] Servidor Linux provisionado (SSH liberado, porta 443 aberta)
- [ ] Subdomínio criado e DNS apontando pro servidor
- [ ] Python 3.12+, Nginx e Certbot instalados
- [ ] Código clonado em `/opt/dashboard-mkt-raviera`
- [ ] `.env` e `raviera.db` recebidos do Felipe por canal seguro e colocados em `backend/`
- [ ] Ambiente virtual criado e dependências instaladas
- [ ] Serviço systemd configurado e rodando (`systemctl status dashboard-raviera`)
- [ ] Nginx configurado e certificado HTTPS emitido
- [ ] Painel acessível em `https://dashboard.gruporaviera.com.br`
- [ ] Rodapé do sistema mostrando a versão esperada (ex.: "v0.44")
- [ ] Webhooks do Followize cadastrados (depois de confirmar que o domínio está no ar)
