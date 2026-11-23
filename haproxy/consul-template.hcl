consul {
  address = "consul:8501"
  ssl {
    enabled = true
    verify = true
    ca_cert = "/tls/ca.crt"
  }
}

template {
  source = "/usr/local/etc/haproxy/haproxy.ctmpl"
  destination = "/tmp/haproxy.cfg"
}

exec {
  command = ["haproxy", "-W", "-db", "-f", "/tmp/haproxy.cfg"]
  reload_signal = "SIGUSR2"
  kill_signal = "SIGTERM"
}
