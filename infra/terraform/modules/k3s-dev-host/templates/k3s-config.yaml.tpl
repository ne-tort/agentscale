# kubelet: защита узла от глобального OOM.
#
# Инцидент 2026-10-23 (prod): нода ушла в глобальный OOM при 62 МБ свободной
# памяти, после чего apiserver деградировал настолько, что запросы к Sandbox CR
# висели 19-22 минуты, а kubelet не мог выполнить ListPodSandbox. Снаружи это
# выглядело как «нода зависла по CPU».
#
# Причина структурная: kubelet работал с дефолтами, при которых единственным
# арбитром памяти остаётся ядро (и OOM-killer выбирает жертву по своим правилам),
# а системе не зарезервировано ничего — k3s, containerd и page cache могли быть
# вытеснены рабочими подами.
#
# Что задаём:
#   system-reserved / kube-reserved — вычитаются из allocatable, поэтому
#     планировщик не сможет продать подами память, нужную ОС и kubelet'у;
#   eviction-hard — при memory.available < 1200Mi kubelet немедленно выселяет
#     поды в порядке QoS, НЕ дожидаясь ядерного OOM-killer;
#   eviction-soft — раннее предупреждение: 1800Mi держится 90с → выселение
#     (даёт сервисам шанс отдать кэш, не убивая их);
#   eviction-minimum-reclaim — после выселения kubelet добирает обратно до
#     порога, иначе тряска повторится на следующем цикле.
#
# Числа подобраны под 14.9 ГиБ allocatable: запас 1.5 ГиБ на систему + 1.2 ГиБ
# аварийного порога оставляют рабочим подам ~12 ГиБ, при фактическом
# потреблении 7.9 ГиБ — двойной запас.
kubelet-arg:
  - "system-reserved=memory=1024Mi,cpu=250m"
  - "kube-reserved=memory=512Mi,cpu=250m"
  - "eviction-hard=memory.available<1200Mi,nodefs.available<10%,imagefs.available<15%"
  - "eviction-soft=memory.available<1800Mi"
  - "eviction-soft-grace-period=memory.available=90s"
  - "eviction-max-pod-grace-period=30"
  - "eviction-pressure-transition-period=30s"
  - "eviction-minimum-reclaim=memory.available=256Mi"