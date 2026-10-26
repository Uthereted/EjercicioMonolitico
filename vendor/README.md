# Chasis compartido

`microservice_chassis-0.5.0-py3-none-any.whl` es la distribución probada de
`chasis_homework`. Los seis consumidores Docker instalan esta misma wheel.
No necesitan un checkout hermano ni descargar la rama mutable `main`.

SHA-256: `04d9478b7352f69362974165734b444687f94eb1c0d966ef65b50b9130e9738a`

Para reconstruir desde el repositorio independiente:

```powershell
python -m pip install setuptools wheel
python tools/build_chassis.py --source ../chasis_homework
```

Si cambia la versión del paquete, actualizar también los pins de requisitos y
las rutas de la wheel en los Dockerfile antes de reconstruir las imágenes.
