FROM hashicorp/consul-template:0.41.2 AS consul_template
FROM haproxy:3.2
COPY --from=consul_template /bin/consul-template /usr/local/bin/consul-template
COPY haproxy/consul-template.hcl /usr/local/etc/haproxy/consul-template.hcl
COPY haproxy/haproxy.ctmpl /usr/local/etc/haproxy/haproxy.ctmpl
ENTRYPOINT ["consul-template", "-config=/usr/local/etc/haproxy/consul-template.hcl"]
