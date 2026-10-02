<p align="center">
 <img width="100px" src="logo.svg" align="center" alt="Logo" />
 <h2 align="center">Chinese Building Energy-Efficient Design Datasets (CBED)</h2>
 <p align="center">A FAIR Framework for Transparent, Secure Sharing of Chinese Building Energy-Efficient Design Datasets</p>
</p>
<p align="center">
  <a href="https://buildingdata.xauat.edu.cn/"><img alt="buildingdata" src="https://img.shields.io/badge/CBED Platform-063c7c?style=flat&logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAACXBIWXMAAAsTAAALEwEAmpwYAAABQ0lEQVQ4jZ3UPU8UURjF8d8ubwmhopGAQKSDymhjQWm30hEbwkthgV9AQkUJFq5+AyVsIYXfwspADRWSYAMfgIIQH4qdjZv1zp1lT3Izd86c88/cZJ6pRYQ+9BY1HFcmI6JqvYl/WqnKV8Ga8b+agwCfRcRpAtbRaUQs9AvcyIB6tVkF/PYIWEeHKeCLiLgYANbR74JBRGxXhP9ExExEzEbEVUX2fR0rma/qEk/xCg3M4jyTbwzjOhNYwBpaxf0cFlE2DTf1DOxzUWx1ebuYLq4p1XLAA6wm/H18Len8zQFv8DrhL2u/+X3i2V0OCGMJ7x4j2j+LXmWPPIFfCf8E4xhKlXLALRwm/D08LyvlgJ9wi6Mev4GdQYCj+IgNnHX5X/CyrFPHkwz0A9axhO+ZXEdTw/ihfFrGMVns32mP3XwG+PMBYdAQPwkBdngAAAAASUVORK5CYII=&logoColor=white" /></a>
  <a href="https://github.com/buildingdata"><img alt="Github" src="https://img.shields.io/badge/GitHub-100000?style=flat&logo=github&logoColor=white" /></a>
  <a href="https://github.com/buildingdata/CBED-portal/blob/master/LICENSE"><img alt="License" src="https://img.shields.io/github/license/buildingdata/CBED-portal.svg?style=flat" /></a>
  <a href="https://github.com/buildingdata/CBED-portal/releases"><img src="https://img.shields.io/github/v/release/buildingdata/CBED-portal?label=version&style=flat" /></a>
  <a href="https://doi.org/10.5281/zenodo.15401815"><img src="https://zenodo.org/badge/DOI/10.5281/zenodo.15401815.svg" alt="DOI"></a>
</p>

### Abstract

Building energy-efficient design data are foundational for advancing green building practices. The design and performance modeling for green building require diverse and precise datasets, including long-term meteorological records, thermal and HVAC design condition datasets, typical meteorological year (TMY) files for simulation, and occupant thermal comfort indices. Generating such data demand high-resolution meteorological and behavioral data, coupled with complex computational methods. However, data accessibility is often limited by national policies and privacy concerns. To overcome these limitations, the CBED (Chinese Building Energy-Efficient Design Datasets) platform, supported by the Ministry of Science and Technology of China, integrates building science methodologies with the FAIR (Findable, Accessible, Interoperable and Reusable) principles to promote data transparency and interoperability. The platform employs automated data engineering workflows based on the SSM framework, incorporating built-in quality assurance mechanisms and SSL/TLS encryption to ensure security. By coupling computational methods with FAIR-compliant data governance, CBED provides a reproducible model for global collaboration. It enables researchers to access and contribute high-quality datasets, thereby accelerating innovation in sustainable building technologies.

### Repository

The database files are located in the `/sql` directory, and the open source code for the site is in the `/web` directory, which has the following directory structure.

```
/sql
/web
├── /frontend        # front-end
├── /app_front       # mobile-app
└── /backend         # back-end
```

### Installation

#### 1. Clone

Clone this repository and sql import the data tables to mysql:

```bash
git clone https://github.com/buildingdata/CBED-portal.git
```

#### 2. Download dependencies

Navigate to the `/web/backend` project directory and run it:

```bash
mvn clean install
```

This will download all required dependencies and compile the project.

#### 3. Run project

Run the application using the following command:

```bash
mvn spring-boot:run
```

This will start the embedded server (usually Tomcat).

#### 4. View

Once the server is up, open your browser and go to:

```bash
http://localhost:8080
```

### Open data

This code repository is shared for [scientific data](https://www.nature.com/sdata/).

 <img width="150px" src="scidata.svg" align="right" alt="Logo" />

### License

The code is available under the [GPL-3.0 license](https://github.com/buildingdata/CBED-portal/blob/master/LICENSE)
