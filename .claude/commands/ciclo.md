---
description: Executa um ciclo completo de desenvolvimento (planejar, implementar, testar, revisar, corrigir)
---

Execute o ciclo de desenvolvimento do Inovalytics para a próxima tarefa do backlog${ARGUMENTS:+, com foco em: $ARGUMENTS}.

1. Acione o subagente `planejador` e obtenha a tarefa e os critérios de aceite.
2. Acione o `implementador` com essa tarefa.
3. Acione o `testador`.
4. Acione o `revisor`.
5. Se houver falhas ou o revisor reprovar, acione o `corretor` e repita os passos 3 e 4. Máximo de 3 ciclos de correção.
6. Se `make check` estiver verde e o revisor APROVAR, marque a tarefa como concluída em `docs/backlog.md` e faça o commit.
7. Se passar de 3 ciclos, pare e resuma o impasse para o humano.

Ao final, responda com 3 linhas: tarefa, resultado, próxima tarefa sugerida.
