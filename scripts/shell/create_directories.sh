# Create default directories.

# Array of top-level directory names to create.
directories=(".github" "backend" "caches" "cicd" "data" "docs" "examples" "frontend" "graveyard" "logs" "results" "secrets")

# Create each top-level directory.
for dir in "${directories[@]}"; do
  # Check if the directory exists.
  if [ ! -d "$dir" ]; then
    echo "Creating $dir directory..."
    mkdir "$dir"
  fi
done

# Create 'src' directory exists inside 'backend'.
if [ ! -d "backend/src" ]; then
  echo "Creating backend/src directory..."
  mkdir -p "backend/src"
fi

# Create 'tests' directory exists inside 'backend'.
if [ ! -d "backend/tests" ]; then
  echo "Creating backend/tests directory..."
  mkdir -p "backend/tests"
fi

# Create 'src' directory inside 'frontend'.
if [ ! -d "frontend/src" ]; then
  echo "Creating frontend/src directory..."
  mkdir -p "frontend/src"
fi

# Create 'public' directory inside 'frontend'.
if [ ! -d "frontend/public" ]; then
  echo "Creating frontend/public directory..."
  mkdir -p "frontend/public"
fi

# Create 'tests' directory inside 'frontend'.
if [ ! -d "frontend/tests" ]; then
  echo "Creating frontend/tests directory..."
  mkdir -p "frontend/tests"
fi

# Create sub-directories inside 'frontend/src'.
frontend_subdirs=("components" "features" "hooks" "layouts" "pages" "services" "styles" "types" "utils")
for subdir in "${frontend_subdirs[@]}"; do
  if [ ! -d "frontend/src/$subdir" ]; then
    echo "Creating frontend/src/$subdir directory..."
    mkdir -p "frontend/src/$subdir"
  fi
done
