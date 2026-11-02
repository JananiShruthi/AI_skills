class Base:
    def save(self, x):
        if x is None:
            raise ValueError("x")
        return x
