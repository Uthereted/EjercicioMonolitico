# Monolithic Manufacturing application

* If you want to be able to debug the app,
or you don't know anything about **Docker** and **Docker Compose**, 
you can install **PyCharm IDE** and follow **Running Monolithic application using PyCharm IDE**.
* If you want to use **Docker** and **Docker Compose**,
you can follow **Running Monolithic application using Docker Compose**.


## Running Monolithic application using PyCharm IDE

* If you have a project opened: `File > Close project`.
* Click `Get from Version Control` and set *https://gitlab.com/macc_ci_cd/aas/monolithic.git*
  as repository.
* Once the project is loaded, create a virtual environment (**venv**) at `File > Settings > Project: monolithic > Python Interpreter`.
* Set `fastapi_app` folder as **Sources Root** (right click on folder, `Marc Directory as > Sorces Root`)
* Add the packages in `fastapi_app > requirements.txt` to de **venv**.
  * Open the terminal (usually at the bottom left of the IDE)
  * Go to monolithic folder: `cd fastapi_app`
  * Install the dependencies: `pip install -r requirements.txt`
* Configure Run/Debug. We will use **Hypercorn** as server, and we will pass our app class in app.main as our application:
  * Click over Run/Debug configuration and select `Edit Configurations...`.
  * Click on the `+` sign and select `Python`.
  * Check that the interpreter is the local interpreter.
  * Chose module instead of script and write `hypercorn`.
  * In parameters write `app.main:app --bind 0.0.0.0:8000`
    * This is the same as we have in `entrypoint.sh`.
  * In working directory write `$ProjectFileDir$/fastapi_app`
  * In environment variables write `PYTHONUNBUFFERED=1;SQLALCHEMY_SQLITE_DATABASE_URI=sqlite+aiosqlite:////volume/monolithic.db`
    * This is similar to what we have in `dot_env_example` file.
  * Click on the `Bug` icon to run the application on debug mode.
    * If you have a breakpoint in the code (e.g. line 20 in `main.py`), the execution will stop there when you start the application.

You can continue with **Understanding the repository** and **REST API Method** points in this readme.

## Running Monolithic application with docker compose

* If you did not do it yet, create aas folder and clone repository from GitLab:

```bash
mkdir aas
cd aas
git clone https://gitlab.com/macc_ci_cd/aas/monolithic.git
cd monolithic
```

* Copy environment variables to .env file:

```bash
cp dot_env_example .env
```

* Launch monolithic application using docker compose:

```bash
docker compose up -d --build
```

## Understanding this repository

### Docker related files

* **`compose.yml`**: indicates how to create the container(s) the application has,
which port to use...
* **`dot_env_example`**: it has to be copied (and renamed to *.env*) to the needed path 
for the application to know environment variables.
* **`fastapi_app > Dockerfile`**: it has the docker commands to create the image with 
our FastAPI application and needed Dependencies. I also defines that when the container is run,
`hypercorn` server has to be executed.
* **`fastapi_app > entrypoint.sh`**: it is the script that will be executed when the container starts.
It will execute hypercorn with the FastAPI application.
Hypercorn is a web server that is used to serve the FastAPI application.

> FastAPI uses lifespan events for starting and finishing the application in a correct way.
> In order to do that, hypercorn has to be also correctly closed.
> That is why entrypoint.sh has a "trap", in order to close hypercorn correctly.
> When container receives a signal to finish, it is captured and hypercorn is notified to finish. 
>
> ```sh
> ...
> terminate() {
>   echo "Termination signal received, shutting down..."
>   kill -SIGTERM "$HYPERCORN_PID"
>   wait "$HYPERCORN_PID"
>   echo "Hypercorn has been terminated"
> }
>
> trap terminate SIGTERM SIGINT
>
> hypercorn \
>   --bind 0.0.0.0:8000 \
>   app.main:app &
> 
> # Capture the PID of the Hypercorn process
> HYPERCORN_PID=$!
>
> # Wait for the Hypercorn process to finish
> wait "$HYPERCORN_PID"
> ```

### Monolithic (`fastapi_app`)

* **`main.py`**: the main function that will initiate the FastAPI application.
* **`requirements.txt`**: The dependencies that are needed to execute the application. 
The Dockerfile will execute `pip install -r requirements.txt` to install them
when we build the image.

### Application (`fastapi_app > app`)

* **`dependencies.py`**: functions to inject dependencies to FastAPI (e.g. DB Session).
This is very interesting, for example, when you want to use a different database for testing.
* **`business_logic/async_machine.py`**: this is the coroutine that will simulate the manufacturing
process. It includes functions to manage pieces and a queue of pieces to manufacture.
* **`routers/main_router.py`**: contains the REST API endpoint definitions for the application:
  GET, POST, DELETE... You could separate this in multiple files.
* **`routers/router_utils.py`**: contains different router utility functions that could be used
in different routers.
* **`sql/crud.py`**: this file contains the functions that access the database.
* **`sql/database.py`**: this file contains the database configuration.
* **`sql/models.py`**: this file contains the mapping between Python Objects and database 
tables.
* **`sql/schemas.py`**: this file contains the schema definitions for endpoint request and
responses (e.g. defines the json structure for the request/responses of the app).

## REST API Methods

If you execute the app the host will be *localhost*. The port may 
vary:

* **8000** if executed from the IDE.
* **13000** if executed using Docker Compose.

One of the main advantages of FastAPI is that it creates the API documentation automatically if 
you do it right. Execute the application and open 
[http://localhost:8000/docs](http://localhost:8000/docs) or [http://localhost:13000/docs](http://localhost:13000/docs).
Open that page, so you can see the endpoints of the app. You could test how it works from there.

Nevertheless, we recommend:

* Install an application to test the REST APIs. e.g.:

  * [Bruno](https://www.usebruno.com/) (Free and OpenSource + PRO Version, Git friendly)
    * There is a collection of api calls in this repository at `docs/bruno_collection`.
    * You can import it in Bruno.
    * Choose IDE or Docker Compose environments (so correct url is used).
    * {{orderId}} and {{pieceId}} are request variables that will be created when creating or listing elements.
    * There are Asserts for each request to check that the response is correct.
    * POST order has also a Test example, but this is for more complex scenarios.
  * [Postman](https://www.getpostman.com/) (Free, you can program environment variables)
  * [Insomnia](https://insomnia.rest/) (Free and OpenSource + PRO Version)
  * [SOAP UI](https://www.soapui.org/) (Free and OpenSource + PRO Version)

The following methods can be seen at
`fastapi_app > app > routers > main_router.py` annotations.


### Create an order [POST]

* URL: *http://localhost:13000/order*
* Body:

```json
{
  "description": "New order created from REST API",
  "number_of_pieces": 5
}
```

### View an Order [GET]:

You can get the ID when you create the order.
* URL: *http://localhost:13000/order/{id}*

### Remove an Order [DELETE]:

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

### View all Pieces [GET]:

* URL: *http://localhost:13000/piece*

### View a Piece [GET]:

You can get the ID when you create the order.
* URL: *http://localhost:13000/piece/{id}*



## Help on docker commands

> Launch docker compose:
>
> ```bash
> docker compose -f "file" up
> ```
>
> or (if there is a compose.yml file in the current folder)
>
> ```bash
> docker compose up
> ```
>
> If you want to run it in the background so you can keep using the terminal, 
>  run it as a daemon adding `-d`:
>
> ```bash
> docker compose up -d
>```
> 
> If we make changes to the image but we do not build it, 
> we may be using an old version without knowing it. 
> If you want to asure that you are using the latest version, you can **rebuild** the image:
> 
> ```bash
> docker compose up -d --build
> ```
> As Docker images are built in layers, the speed of the build process will depend on the changes made.
> If there are no changes in the Dockerfile, the process will be instantaneous.

> List running containers:
> 
> ```bash
> docker compose ps
> ```

> Stop one of the containers:
>
> ```bash
> docker compose stop <container-name>
>
> docker compose stop monolithicapp
> ```

> Stop all containers:
>
> ```bash
> docker compose -f "file" down
> ```
>
> or (if there is a compose.yml file in the current folder)
>
> ```bash
> docker compose down
> ```