from passlib.hash import sha512_crypt
# Generar para el usuario esp32 y gui_operator
h1 = sha512_crypt.using(rounds=1000).hash('FRANCISCO')
h2 = sha512_crypt.using(rounds=1000).hash('FRANCISCO')
with open(r'C:\mosquitto-broker\passwd', 'w') as f:
    f.write('esp32:' + h1 + '\n')
    f.write('gui_operator:' + h2 + '\n')
print('Archivo generado correctamente')
print('esp32:' + h1)
print('gui_operator:' + h2)
