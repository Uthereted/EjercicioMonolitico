# Monolithic Manufacturing application

* If you want to be able to debug the app,
or you don't know anything about **Docker** and **Docker Compose**, 
you can install **PyCharm IDE** and follow **Running Monolithic application using PyCharm IDE**.
* If you want to use **Docker** and **Docker Compose**,
you can follow **Running Monolithic application using Docker Compose**.


## Running Monolithic application using PyCharm IDE

* If you have a project opened: ```File > Close project```.
* If you have GitLab access:
  * Click ```Get from Version Control``` and put
*https://gitlab.com/macc_ci_cd/aas/monolithic.git* or *git@gitlab.com:macc_ci_cd/aas/monolithic.git*
as repository.
* If you don't have gitlab access:
  * Unzip the ZIP file and open it using PyCharm.
* Once the project is loaded, create a virtual environment (**venv**) at ```File > Settings > Project: monolithic > Python Interpreter```.
* Set ```fastapi_app > monolithic``` as Sources root (right click on folder, ```Marc Directory as > Sorces Root```)
* Add the packages in ```fastapi_app > monolithic > requirements.txt``` to de **venv**.
  * in the terminal (usually at the bottom of the IDE) ```cd fastapi_app/monolithic```
  * Then install dependencies ```pip install -r requirements.txt```
* Click ```OK``` button.
* Copy environment variables to needed folder:
  * Right click on ```dot_env_exam``` file.
  * Paste it inside ```fastapi_app > monolithic``` and name it as ```.env```.
* Run or Debug main.py

You can continue with **Understanding the repository** and **REST API Method** points in this readme.

## Running Monolithic application with docker compose

* Create aas folder and clone repository from GitLab:

```bash
mkdir aas
cd aas
git clone https://gitlab.danz.eus/macc/cloud-computing/advanced-software-architectures/aas/monolithic.git
cd monolithic
```

* Copy environment variables to .env file:

```bash
cp dot_env_example .env
```

* Launch monolithic application using docker compose:

```bash
docker compose up -d
```

## Help on docker commands

* Launch docker compose:

```bash
docker compose -f "file" up
```

or (if there is a compose.yml file in the current folder)

```bash
docker compose up
```

* If you want to run it in the background (daemon) add ```-d```:

```bash
docker compose up -d
```

* View running containers:

```bash
docker compose ps
```

* Stop one of the containers:

```bash
docker compose stop <container-name>

docker compose stop monolithicapp
```

* Stop docker compose:

```bash
docker compose -f "file" down
```

or (if there is a compose.yml file in the current folder)

```bash
docker compose down
```

## Understanding this repository

### Docker related files

* **```compose.yml```**: indicates the images/containers the application has,
which port to use...
* **```dot_env_example```**: it has to be copied (and renamed to *.env*) to the needed path 
for the application to know environment variables
* **```fastapi_app > Dockerfile```**: it has the docker commands to create the image with 
our FastAPI application and needed Dependencies. I also defines that when the container is run,
```hypercorn``` server has to be executed.

### Monolithic (```fastapi_app > monolithic```)

* **```main.py```**: the main function that will initiate the FastAPI application.
* **```requirements.txt```**: The dependencies that are needed to execute the application. 
The Dockerfile will execute ```pip install -r requirements.txt``` to install them
when we build the image.

### Application (```fastapi_app > monolithic > app```)

* **```dependencies.py```**: Functions to inject dependencies to FastAPI (e.g. DB Session).
This is very interesting, for example, when you want to use a different database for testing.
* **```business_logic/async_machine.py```**: This is the coroutine that will simulate the manufacturing
process. It includes functions to manage pieces and a queue of pieces to manufacture.
* **```routers/main_router.py```**: contains the REST API endpoint definitions for the application:
  GET, POST, DELETE... You could separate this in multiple files.
* **```routers/router_utils.py```**: contains different router utility functions that could be used
in different routers.
* **```sql/crud.py```**: this file contains the functions that access the database.
* **```sql/database.py```**: this file contains the database configuration.
* **```sql/models.py```**: this file contains the mapping between Python Objects and database 
tables.
* **```sql/schemas.py```**: this file contains the schema definitions for endpoint request and
responses (e.g. defines the json structure for the request/responses of the app).

## REST API Methods

One of the main advantages of FastAPI is that it creates the API documentation automatically if 
you do it right. Execute the application and open 
[http://localhost:8000/docs](http://localhost:8000/docs) or [http://localhost:13000/docs](http://localhost:13000/docs).
Open that page, so you can see the endpoints of the app. You could test how it works from there.

Nevertheless, we recommend:

* Install an application to test the REST APIs. e.g.:

  * [Postman](https://www.getpostman.com/) (Free, you can program environment variables)
  * [Insomnia](https://insomnia.rest/) (Free and OpenSource + PRO Version)
  * [SOAP UI](https://www.soapui.org/) (Free and OpenSource + PRO Version)

* Or using the http file:
  * PyCharm Professional (example at ```docs/rest.http```).
  * VSCode (example at ```docs/rest_vscode.http```).

The following methods can be seen at
```fastapi_app > monolithic > app > routers > main_router.py``` annotations.

If you execute the app in your IDE, the host will be *localhost* and port may 
vary (8000, 13000...).

## Create an order [POST]

* URL: *http://localhost:13000/order*
* Body:

```json
{
  "description": "New order created from REST API",
  "number_of_pieces": 5
}
```

## View an Order [GET]:

You can get the ID when you create the order.
* URL: *http://localhost:13000/order/{id}*

## Remove an Order [DELETE]:

You can get the ID when you create the order.
* URL: *http://localhost:13000/order/{id}*

The order will be deleted and the un-manufactured pieces will be removed from the machine queue.
All the un-manufactured pieces of the order will appear as *"Cancelled"* and the order_id
will be *null*.

### View all Orders [GET]:

* URL: *http://localhost:13000/order*

### View Machine Status [GET]:

You can see the queue, the status of the machine and the piece that is being manufactured.
* URL: *http://localhost:13000/machine/status*

### View a Piece [GET]:

You can get the ID when you create the order.
* URL: *http://localhost:13000/piece/{id}*

### View all Pieces [GET]:

* URL: *http://localhost:13000/piece*
