
Copy SprinklerController.service into /etc/systemd/system as root, for example:

> sudo cp SprinklerController.service /etc/systemd/system/SprinklerController.service

Once this has been copied, you can attempt to start the service using the following command:

> sudo systemctl start SprinklerController.service

Stop it using following command:

sudo systemctl stop SprinklerController.service

To have it start automatically on reboot by using this command:

sudo systemctl enable SprinklerController.service

The systemctl command can also be used to restart the service or disable it

To reboot the pi:

sudo reboot
